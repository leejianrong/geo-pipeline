import argparse
import sys
import time

from . import bus, store
from .jobs import submit_job
from .messages import Options


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://") or "/" not in uri[5:]:
        raise SystemExit(f"expected s3://bucket/key, got {uri!r}")
    bucket, key = uri[5:].split("/", 1)
    return bucket, key


def main() -> None:
    p = argparse.ArgumentParser(prog="geopipe")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="queue a GeoTIFF for detection")
    s.add_argument("uri")
    s.add_argument("--tenant", default="local")
    s.add_argument("--model", default="stub")
    s.add_argument("--tile-size", type=int, default=640)
    s.add_argument("--overlap", type=float, default=0.2)
    st = sub.add_parser("status", help="show a job's status")
    st.add_argument("job_id")
    up = sub.add_parser("upload", help="upload a local file to s3://bucket/key")
    up.add_argument("path")
    up.add_argument("uri")
    dl = sub.add_parser("download", help="download s3://bucket/key to a local file")
    dl.add_argument("uri")
    dl.add_argument("path")
    v = sub.add_parser("viz", help="write PNG plots for finished jobs")
    v.add_argument("job_ids", nargs="+")
    v.add_argument("--out", default="viz", help="output directory (default: ./viz)")
    b = sub.add_parser("bench", help="score the detectors on COCO images through the pipeline")
    b.add_argument("--annotations", required=True, help="COCO instances_val2017.json")
    b.add_argument("--images", default="coco-images", help="directory of COCO images")
    b.add_argument("--models", default="rfdetr,yolox")
    b.add_argument("--limit", type=int, default=200, help="number of images")
    b.add_argument("--conf", type=float, default=0.05)
    b.add_argument("--iou", type=float, default=0.65)
    b.add_argument("--download", action="store_true", help="fetch missing images from COCO")
    b.add_argument("--out", default="bench-out")
    b.add_argument("--title", help="report title (default: the COCO one)")
    b.add_argument("--caveat", help="report caveat (default: the COCO one)")
    b.add_argument("--dataset-id", help="also store each model's run for this registered dataset")
    b.add_argument("--split", default="test", help="split of that dataset (default: test)")
    sp = sub.add_parser("spacenet-prepare",
                        help="write COCO training data and test.json from SpaceNet tiles")
    sp.add_argument("root", help="a directory made by scripts/fetch_spacenet.py")
    sp.add_argument("out")
    rb = sub.add_parser("register-builtin", help="register the SpaceNet dataset for the studio")
    rb.add_argument("coco", help="the output directory of spacenet-prepare")
    rb.add_argument("--bucket", default="imagery")
    w = sub.add_parser("wait", help="wait for a job: exit 0 done, 2 incomplete, 1 error")
    w.add_argument("job_id")
    w.add_argument("--timeout", type=int, default=120)
    a = p.parse_args()

    if a.cmd in ("upload", "download"):
        s3 = store.client()
        bucket, key = _split(a.uri)
        if a.cmd == "upload":
            store.ensure_bucket(s3, bucket)
            s3.upload_file(a.path, bucket, key)
        else:
            s3.download_file(bucket, key, a.path)
        return

    if a.cmd == "spacenet-prepare":
        from pathlib import Path

        from . import spacenet  # needs GDAL

        print(spacenet.prepare(Path(a.root), Path(a.out)))
        return

    r = bus.connect()
    if a.cmd == "register-builtin":
        from pathlib import Path

        from . import registry

        s3 = store.client()
        store.ensure_bucket(s3, a.bucket)
        registry.register_builtin_models(s3, r, a.bucket, "spacenet-khartoum")
        print(registry.register_dataset(
            s3, r, a.bucket, Path(a.coco), "spacenet-khartoum", name="SpaceNet Khartoum buildings",
            licence="CC BY-SA 4.0", attribution=registry.SPACENET_ATTRIBUTION))
        return
    if a.cmd == "bench":
        import json
        from pathlib import Path

        from . import bench  # needs the `bench` extra and GDAL

        with open(a.annotations) as f:
            coco = json.load(f)
        report = {k: v for k, v in (("title", a.title), ("caveat", a.caveat)) if v}
        register = (a.dataset_id, a.split) if a.dataset_id else None
        bench.run_benchmark(r, store.client(), coco, a.models.split(","), Path(a.images),
                            Path(a.out), a.limit, a.conf, a.iou, a.download, register, **report)
        print(Path(a.out) / "report.md")
    elif a.cmd == "viz":
        from . import viz  # needs the `viz` extra (matplotlib) and GDAL

        s3 = store.client()
        print(viz.write_plots([viz.load_job(r, s3, j) for j in a.job_ids], a.out))
    elif a.cmd == "submit":
        bucket, key = _split(a.uri)
        opts = Options(model=a.model, tile_size=a.tile_size, overlap=a.overlap)
        print(submit_job(r, store.client(), bucket, key, a.tenant, opts))
    elif a.cmd == "status":
        print(r.hgetall(f"job:{a.job_id}:status") or "unknown job")
    else:
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            status = r.hgetall(f"job:{a.job_id}:status")
            if status.get("state") in ("done", "incomplete", "error"):
                print(status)
                sys.exit({"done": 0, "incomplete": 2}.get(status["state"], 1))
            time.sleep(1)
        sys.exit("timed out")
