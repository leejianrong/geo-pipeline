"""The real training function of the GPU trainer (ADR-0016): fetch the dataset, run the trainer
as a subprocess while turning its output into progress, export ONNX, register the model.

Everything here is plain Python around subprocesses, so it is tested on the host with a fake
command; the torch-heavy work happens in the scripts the image carries (`TRAINER_SCRIPTS`).
"""

import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import bus, registry
from .messages import TrainJob
from .presets import PRESETS
from .train import Cancelled, TrainContext, TrainFn
from .trainlog import YoloxLog, rfdetr_progress

TAIL = 8  # lines of a failed trainer's output shown to the owner
POLL_S = 0.5


def run_command(cmd: Sequence[str], *, env: Mapping[str, str] | None = None,
                cwd: str | Path | None = None, on_line: Callable[[str], None] = lambda _: None,
                tick: Callable[[], None] = lambda: None,
                cancelled: Callable[[], bool] = lambda: False, poll: float = POLL_S) -> list[str]:
    """Run `cmd`, calling `on_line` for each line of its output and `tick` about every `poll`
    seconds. Raises `Cancelled` (after stopping the process group) when `cancelled()` turns true,
    and `bus.PermanentError` with the output's tail if it exits non-zero. Returns all lines."""
    proc = subprocess.Popen(cmd, env={**os.environ, **(env or {})}, cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
                            start_new_session=True)  # its own group, so cancel stops children too
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    seen: list[str] = []
    done = False
    try:
        while not done:
            try:
                line = lines.get(timeout=poll)
            except queue.Empty:
                line = ""
                tick()
            else:
                if line is None:
                    done = True
                    continue
                seen.append(line)
                on_line(line)
            if cancelled():
                raise Cancelled
        if proc.wait() != 0:
            tail = " | ".join(x.strip() for x in seen[-TAIL:] if x.strip())
            raise bus.PermanentError(f"training failed (exit {proc.returncode}): {tail}")
        tick()
        return seen
    except BaseException:
        _stop(proc)
        raise


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def download_dataset(s3, bucket: str, scope: str, dataset_id: str, dest: Path) -> Path:
    """Copy a dataset's split folders (images and annotations, not thumbnails) to `dest`."""
    base = registry.prefix(scope, dataset_id) + "/"
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=base):
        keys += [o["Key"] for o in page.get("Contents", [])
                 if o["Key"].split("/")[len(base.split("/")) - 1] in registry.SPLITS]

    def fetch(key: str) -> None:
        target = dest / key[len(base):]
        target.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(target))

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(fetch, keys))
    return dest


def train_and_validation_split(root: Path) -> tuple[Path, Path]:
    """The folders to train and validate on: `valid` if there is one, else `test`."""
    train = root / "train"
    valid = next((root / s for s in ("valid", "test") if (root / s).is_dir()), None)
    if not train.is_dir() or valid is None:
        raise bus.PermanentError("the dataset needs a train split and a valid or test split")
    return train, valid


def make_train_fn(s3, scripts: Path | None = None, work: Path | None = None) -> TrainFn:
    """The training function for a worker with `s3`. `scripts` holds the training and export
    scripts, `work` is scratch space (a run needs the dataset and checkpoints on disk)."""
    scripts = scripts or Path(os.environ.get("TRAINER_SCRIPTS", "/scripts"))
    work = work or Path(os.environ.get("TRAINER_WORK", "/tmp/train"))

    def train(job: TrainJob, ctx: TrainContext) -> str:
        preset = PRESETS[job.preset]
        scope, dataset = registry.find(s3, job.bucket, job.tenant_id, job.dataset_id)
        run_dir = work / job.job_id
        shutil.rmtree(run_dir, ignore_errors=True)
        try:
            data = download_dataset(s3, job.bucket, scope, job.dataset_id, run_dir / "data")
            train_dir, valid_dir = train_and_validation_split(data)
            out = run_dir / "out"
            if preset.decoder == "yolox":
                onnx = _train_yolox(job, ctx, preset, dataset, scripts, train_dir, valid_dir, out)
            else:
                onnx = _train_rfdetr(job, ctx, preset, dataset, scripts, data, valid_dir, out)
            return register_model(s3, ctx.r, job.bucket, job, dataset, onnx)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    return train


def _report(ctx: TrainContext, update: dict | None, epochs: int) -> None:
    if update:
        ctx.progress(update["epoch"], val_ap=update.get("val_ap"), loss=update.get("loss"),
                     epochs=epochs)


def _train_yolox(job, ctx, preset, dataset, scripts, train_dir, valid_dir, out) -> Path:
    exp = {"YOLOX_VARIANT": preset.variant, "YOLOX_SIZE": str(preset.input_size),
           "YOLOX_EPOCHS": str(job.epochs), "YOLOX_CLASSES": str(len(dataset.classes)),
           "YOLOX_TRAIN": str(train_dir), "YOLOX_VALID": str(valid_dir),
           "YOLOX_OUT": str(out), "YOLOX_EXP_NAME": "run"}
    log = YoloxLog()
    run_command(["python", "/opt/yolox/tools/train.py", "-f", "yolox_exp.py", "-d", "1", "-b",
                 "16", "--fp16", "-c", f"/models/yolox_{preset.variant}.pth"],
                env=exp, cwd=scripts, cancelled=ctx.cancelled,
                on_line=lambda line: _report(ctx, log.feed(line), job.epochs))
    onnx = out / "run" / "model.onnx"
    run_command(["python", "export_yolox_onnx.py", "yolox_exp.py",
                 str(out / "run" / "best_ckpt.pth"), str(onnx)], env=exp, cwd=scripts,
                cancelled=ctx.cancelled)
    return onnx


def _train_rfdetr(job, ctx, preset, dataset, scripts, data, valid_dir, out) -> Path:
    staged = data.parent / "rfdetr_data"  # RF-DETR wants train, valid and test folders
    staged.mkdir()
    for name, src in (("train", data / "train"), ("valid", valid_dir),
                      ("test", data / "test" if (data / "test").is_dir() else valid_dir)):
        (staged / name).symlink_to(src)
    metrics, reported = out / "metrics.csv", 0

    def tick() -> None:
        nonlocal reported
        if metrics.exists():
            for update in rfdetr_progress(metrics.read_text())[reported:]:
                _report(ctx, update, job.epochs)
            reported = len(rfdetr_progress(metrics.read_text()))

    run_command(["python", "train_rfdetr.py", "--data", str(staged), "--variant", preset.variant,
                 "--out", str(out), "--epochs", str(job.epochs), "--classes",
                 ",".join(dataset.classes)], cwd=scripts, tick=tick, poll=5.0,
                cancelled=ctx.cancelled)
    lines = run_command(["python", "export_rfdetr_onnx.py", "--variant", preset.variant,
                         "--weights", str(out / "checkpoint_best_total.pth"), "--out",
                         str(out / "onnx")], cwd=scripts, cancelled=ctx.cancelled)
    wrote = next((x.removeprefix("wrote ").strip() for x in reversed(lines)
                  if x.startswith("wrote ")), "")
    if not wrote or not Path(wrote).is_file():
        raise bus.PermanentError("the RF-DETR export wrote no ONNX file")
    return Path(wrote)


def register_model(s3, r, bucket: str, job: TrainJob, dataset: registry.DatasetManifest,
                   onnx: Path, now=time.time) -> str:
    """Upload the ONNX file, then write the model manifest (last, like every manifest) and list
    the model for its tenant. The model inherits the dataset's licence."""
    preset = PRESETS[job.preset]
    model_id = f"model-{job.job_id.removeprefix('train-')}"
    base = registry.model_prefix(job.tenant_id, model_id)
    s3.upload_file(str(onnx), bucket, f"{base}/model.onnx")
    registry.register_model(s3, r, bucket, registry.ModelManifest(
        id=model_id, name=f"{dataset.name}, {preset.label}", decoder=preset.decoder,
        input_size=preset.input_size, classes=dataset.classes, trained_on=job.dataset_id,
        run_id=job.job_id, licence=dataset.licence, created_at=now()), scope=job.tenant_id)
    return model_id
