"""Stage a fine-tuned model and its model card, and optionally upload both to Hugging Face.

Only models that pass the ADR-0014 bar are accepted, judged from the committed SpaceNet results in
`docs/benchmarks/lightweight-detector/`. Without `--upload` it only writes the staging directory
so the card can be read first. The upload uses the `HF_TOKEN` environment variable.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "docs" / "benchmarks" / "lightweight-detector"
REFERENCE = "yolox-buildings"  # YOLOX-s, the model the bar is written against
RUN = "yolox-spacenet-tiny-416"
# Hub file name -> (backend name in the benchmark, local file under data/models/<RUN>, how made)
FILES = {
    "yolox_tiny_buildings.onnx": ("yolox-tiny-buildings", "yolox_buildings.onnx",
                                  "make train-yolox VARIANT=tiny SIZE=416"),
    "yolox_tiny_buildings_int8.onnx": ("yolox-tiny-buildings-int8", "yolox_buildings_int8.onnx",
                                       f"make quantise RUN={RUN}"),
}
CARD = (ROOT / "scripts" / "model_card.md").read_text()  # {placeholders} filled by render_card


def load_results(threads: int) -> dict[str, dict]:
    """Benchmark rows by backend name, from the committed L4 run (it has every row needed)."""
    name = "spacenet-l4-4threads.json" if threads == 4 else "spacenet-l4-1thread.json"
    return json.loads((RESULTS / name).read_text())


def check_bar(name: str, results: dict[str, dict]) -> tuple[float, float]:
    """(speedup over YOLOX-s, mAP@.5 ratio to YOLOX-s) if `name` passes the bar, else SystemExit."""
    if name not in results:
        raise SystemExit(f"{name} is not in the committed results; benchmark it first")
    row, ref = results[name], results[REFERENCE]
    speedup = ref["mean_detect_seconds"] / row["mean_detect_seconds"]
    ratio = row["map50"] / ref["map50"]
    if name.endswith("-int8"):
        base = results[name.removesuffix("-int8")]
        if base["map50"] - row["map50"] > 0.02:
            raise SystemExit(f"{name} loses more than 2 mAP points against its FP32 model")
    elif ratio < 0.9 or speedup < 2:
        raise SystemExit(f"{name} does not pass the bar: {ratio:.0%} of YOLOX-s mAP@.5 "
                         f"(need 90%), {speedup:.1f}x faster (need 2x)")
    return speedup, ratio


def render_card(results4: dict, results1: dict, commit: str) -> str:
    def row(label: str, backend: str) -> str:
        r4, r1 = results4[backend], results1[backend]
        return (f"| {label} | {r4['map50']:.3f} | {r4['f1']:.3f} | "
                f"{r4['mean_detect_seconds']:.2f} | {r1['mean_detect_seconds']:.2f} |")

    rows = [row(backend, backend) for backend, _, _ in FILES.values()]
    rows.append(row(f"{REFERENCE} (YOLOX-s, for comparison)", REFERENCE))
    header = "| model | mAP@.5 | F1@.5 | s/image, 4 threads | s/image, 1 thread |"
    table = "\n".join([header, "|---|---|---|---|---|", *rows])
    files = "\n".join(f"- `{hub}`: `{backend}`, made with `{how}`"
                      for hub, (backend, _, how) in FILES.items())
    speedup, ratio = check_bar("yolox-tiny-buildings", results4)
    return CARD.format(speedup=speedup, ratio=ratio, files=files, table=table, run=RUN,
                       commit=commit, recall=results4["yolox-tiny-buildings"]["recall"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="Hub model repo, e.g. user/yolox-tiny-buildings")
    p.add_argument("--models", default=str(ROOT / "data" / "models"))
    p.add_argument("--stage", default=str(ROOT / "data" / "out" / "hub"))
    p.add_argument("--upload", action="store_true", help="push to the Hub (needs HF_TOKEN)")
    a = p.parse_args()
    r4, r1 = load_results(4), load_results(1)
    for _, (backend, _, _) in FILES.items():
        check_bar(backend, r4)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT, check=True).stdout.strip()
    stage = Path(a.stage)
    stage.mkdir(parents=True, exist_ok=True)
    for hub, (_, local, _) in FILES.items():
        shutil.copy(Path(a.models) / RUN / local, stage / hub)
    (stage / "README.md").write_text(render_card(r4, r1, commit))
    print(f"staged {sorted(f.name for f in stage.iterdir())} in {stage}")
    if not a.upload:
        print("dry run: read the card, then rerun with --upload")
        return
    from huggingface_hub import HfApi  # noqa: PLC0415  (only needed to upload)

    api = HfApi()  # reads HF_TOKEN
    api.create_repo(a.repo, repo_type="model", exist_ok=True)
    api.upload_folder(folder_path=str(stage), repo_id=a.repo, repo_type="model")
    print(f"uploaded to https://huggingface.co/{a.repo}", file=sys.stderr)


if __name__ == "__main__":
    main()
