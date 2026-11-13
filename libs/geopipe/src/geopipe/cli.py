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
    w = sub.add_parser("wait", help="block until a job is done or failed")
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

    r = bus.connect()
    if a.cmd == "viz":
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
            if status.get("state") in ("done", "error"):
                print(status)
                sys.exit(0 if status["state"] == "done" else 1)
            time.sleep(1)
        sys.exit("timed out")
