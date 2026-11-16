"""Stage a fine-tuned model and its model card, and optionally upload both to Hugging Face.

Only models that pass the ADR-0014 bar are accepted, judged from the committed SpaceNet results in
`docs/benchmarks/lightweight-detector/`. Without `--upload` it only writes the staging directory
so the card can be read first. The upload uses `HF_TOKEN` or the login saved by `hf auth login`.

    python scripts/publish_model.py --repo user/yolox-tiny-buildings
    python scripts/publish_model.py --model rfdetr-nano --repo user/rfdetr-nano-buildings
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "docs" / "benchmarks" / "lightweight-detector"
MIN_SPEEDUP = 1.5  # over the reference of the model's own family (ADR-0014, amended 2026-11-16)
MIN_ACCURACY = 0.9  # share of that reference's mAP@.5
# The reference of each family, which the bar is written against: its largest fine-tuned model.
REFERENCES = {"yolox": "yolox-buildings", "rfdetr": "rfdetr-buildings"}
# Published models: the family reference, the data/models folder, and the files to upload as
# Hub file name -> (backend name in the benchmark, local file in that folder, how made).
MODELS = {
    "yolox-tiny": {
        "run": "yolox-spacenet-tiny-416", "card": "model_card.md",
        "files": {"yolox_tiny_buildings.onnx": (
            "yolox-tiny-buildings", "yolox_buildings.onnx",
            "make train-yolox VARIANT=tiny SIZE=416"),
            "yolox_tiny_buildings_int8.onnx": (
                "yolox-tiny-buildings-int8", "yolox_buildings_int8.onnx",
                "make quantise RUN=yolox-spacenet-tiny-416")}},
    "rfdetr-nano": {
        "run": "rfdetr-spacenet-nano", "card": "model_card_rfdetr.md",
        "files": {"rfdetr_nano_buildings.onnx": (
            "rfdetr-nano-buildings", "rfdetr-nano.onnx",
            "make train-rfdetr VARIANT=Nano, then make export-rfdetr RUN=rfdetr-spacenet-nano "
            "VARIANT=Nano")}},
}


def load_results(threads: int) -> dict[str, dict]:
    """Benchmark rows by backend name, from the committed L4 run (it has every row needed)."""
    name = "spacenet-l4-4threads.json" if threads == 4 else "spacenet-l4-1thread.json"
    return json.loads((RESULTS / name).read_text())


def check_bar(name: str, results: dict[str, dict]) -> tuple[float, float]:
    """(speedup, mAP@.5 ratio) over the reference of `name`'s family if it passes the bar, else
    SystemExit. An INT8 model is judged against its own FP32 model instead."""
    if name not in results:
        raise SystemExit(f"{name} is not in the committed results; benchmark it first")
    reference = REFERENCES[name.split("-")[0]]
    row, ref = results[name], results[reference]
    speedup = ref["mean_detect_seconds"] / row["mean_detect_seconds"]
    ratio = row["map50"] / ref["map50"]
    if name.endswith("-int8"):
        base = results[name.removesuffix("-int8")]
        if base["map50"] - row["map50"] > 0.02:
            raise SystemExit(f"{name} loses more than 2 mAP points against its FP32 model")
    elif ratio < MIN_ACCURACY or speedup < MIN_SPEEDUP:
        raise SystemExit(f"{name} does not pass the bar: {ratio:.0%} of {reference} mAP@.5 "
                         f"(need {MIN_ACCURACY:.0%}), {speedup:.2f}x faster (need {MIN_SPEEDUP}x)")
    return speedup, ratio


def render_card(model: str, results4: dict, results1: dict, commit: str) -> str:
    spec = MODELS[model]
    files = spec["files"]
    primary = next(iter(files.values()))[0]
    reference = REFERENCES[primary.split("-")[0]]

    def row(label: str, backend: str) -> str:
        r4, r1 = results4[backend], results1[backend]
        return (f"| {label} | {r4['map50']:.3f} | {r4['f1']:.3f} | "
                f"{r4['mean_detect_seconds']:.2f} | {r1['mean_detect_seconds']:.2f} |")

    rows = [row(backend, backend) for backend, _, _ in files.values()]
    rows.append(row(f"{reference} (for comparison)", reference))
    header = "| model | mAP@.5 | F1@.5 | s/image, 4 threads | s/image, 1 thread |"
    table = "\n".join([header, "|---|---|---|---|---|", *rows])
    listing = "\n".join(f"- `{hub}`: `{backend}`, made with `{how}`"
                        for hub, (backend, _, how) in files.items())
    speedup, ratio = check_bar(primary, results4)
    card = (ROOT / "scripts" / spec["card"]).read_text()  # {placeholders} filled here
    return card.format(speedup=speedup, ratio=ratio, files=listing, table=table, run=spec["run"],
                       commit=commit, recall=results4[primary]["recall"],
                       min_speedup=MIN_SPEEDUP, min_accuracy=f"{MIN_ACCURACY:.0%}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=sorted(MODELS), default="yolox-tiny")
    p.add_argument("--repo", required=True, help="Hub model repo, e.g. user/yolox-tiny-buildings")
    p.add_argument("--models", default=str(ROOT / "data" / "models"))
    p.add_argument("--stage", help="folder to stage into (default: a new temporary folder)")
    p.add_argument("--upload", action="store_true",
                   help="push to the Hub (needs HF_TOKEN or a saved `hf auth login`)")
    a = p.parse_args()
    spec = MODELS[a.model]
    r4, r1 = load_results(4), load_results(1)
    for backend, _, _ in spec["files"].values():
        check_bar(backend, r4)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT, check=True).stdout.strip()
    stage = Path(a.stage or tempfile.mkdtemp(prefix="hub-stage-"))  # data/out is root-owned
    stage.mkdir(parents=True, exist_ok=True)
    for hub, (_, local, _) in spec["files"].items():
        shutil.copy(Path(a.models) / spec["run"] / local, stage / hub)
    (stage / "README.md").write_text(render_card(a.model, r4, r1, commit))
    print(f"staged {sorted(f.name for f in stage.iterdir())} in {stage}")
    if not a.upload:
        print("dry run: read the card, then rerun with --upload")
        return
    from huggingface_hub import HfApi  # noqa: PLC0415  (only needed to upload)

    api = HfApi()  # HF_TOKEN, else the login saved by `hf auth login`
    api.create_repo(a.repo, repo_type="model", exist_ok=True)
    api.upload_folder(folder_path=str(stage), repo_id=a.repo, repo_type="model")
    print(f"uploaded to https://huggingface.co/{a.repo}", file=sys.stderr)


if __name__ == "__main__":
    main()
