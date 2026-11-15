"""Run one real fine-tuning through the stack on the local GPU and print what happened.

    python scripts/gpu_train_run.py --dataset spacenet-khartoum --preset fast [--epochs N]

For the by-hand check of S4 (GEO-70). The one GPU is shared with other work and with the Windows
desktop, and nothing caps VRAM under WSL2, so queue this with pueue (it runs one job at a time),
from a minimal environment:

    env -i PATH="$HOME/.cargo/bin:$PATH" HOME="$HOME" USER="$USER" LOGNAME="$USER" \\
        XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" pueue add -l 'geo-pipeline|train fast' -- \\
        python scripts/gpu_train_run.py --dataset spacenet-khartoum --preset fast

It waits for free VRAM, starts the GPU trainer (the stack must be up), submits the run through the
API, prints the epochs, and cancels the run if free VRAM falls below `--floor-mib`. It restores the
CPU trainer at the end, which frees the GPU. Standard library only.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
GPU = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.gpu.yaml"]
QUEUE_HELPER = (Path.home() / "projects/machine-learning/kaggle/gemma-4-dev-agent"
                / "scripts/gpu_queue.py")


def call(method: str, path: str, key: str, body: dict | None = None) -> dict:
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def ensure_dataset(dataset: str, key: str) -> None:
    """Start the stack if it is down and register the SpaceNet dataset if it is missing (`make e2e`
    wipes both, and this job may start hours after it was queued)."""
    try:
        have = [d["id"] for d in call("GET", "/datasets", key)]
    except OSError:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=True)
        for _ in range(60):
            time.sleep(2)
            try:
                have = [d["id"] for d in call("GET", "/datasets", key)]
                break
            except OSError:
                pass
        else:
            raise SystemExit("the stack did not come up")
    if dataset not in have:
        if dataset != "spacenet-khartoum":
            raise SystemExit(f"dataset {dataset!r} is not registered")
        subprocess.run(["docker", "compose", "run", "--rm", "-T", "tools", "geopipe",
                        "register-builtin", "/data/spacenet/coco"], cwd=ROOT, check=True)


def free_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True).stdout
    used, total = (int(x) for x in out.split(","))
    return total - used


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--preset", default="fast")
    p.add_argument("--epochs", type=int)
    p.add_argument("--key", default="dev-key-a")
    p.add_argument("--wait-util", type=float, default=0.2, help="share of the card to wait for")
    p.add_argument("--floor-mib", type=int, default=600, help="cancel if free VRAM falls below")
    a = p.parse_args()
    if QUEUE_HELPER.exists():
        subprocess.run([sys.executable, str(QUEUE_HELPER), "wait-vram", "--util", str(a.wait_util)],
                       check=True)
    run_id, floor = None, free_mib()
    try:
        ensure_dataset(a.dataset, a.key)
        subprocess.run([*GPU, "up", "-d", "--no-deps", "trainer"], cwd=ROOT, check=True)
        body = {"dataset_id": a.dataset, "preset": a.preset,
                **({"epochs": a.epochs} if a.epochs else {})}
        run_id = call("POST", "/train/runs", a.key, body)["run_id"]
        print("run", run_id, flush=True)
        last = None
        while True:
            run = call("GET", f"/train/runs/{run_id}", a.key)
            floor = min(floor, free_mib())
            line = (run["state"], run.get("epoch"), run.get("val_ap"))
            if line != last:
                print(time.strftime("%H:%M:%S"), *line, f"free VRAM {free_mib()} MiB", flush=True)
                last = line
            if free_mib() < a.floor_mib and run["state"] == "running":
                print("free VRAM below the floor: cancelling", flush=True)
                call("POST", f"/train/runs/{run_id}/cancel", a.key)
            if run["state"] in ("done", "failed", "cancelled"):
                print(json.dumps({k: run.get(k) for k in (
                    "id", "state", "epochs", "epoch", "val_ap", "loss", "model_id", "error")}))
                print("lowest free VRAM seen:", floor, "MiB")
                return 0 if run["state"] == "done" else 1
            time.sleep(10)
    finally:
        if run_id:  # a run still going when this script dies must not keep the GPU
            try:
                call("POST", f"/train/runs/{run_id}/cancel", a.key)
            except Exception:  # already over, or the API is gone
                pass
        subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "trainer"], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
