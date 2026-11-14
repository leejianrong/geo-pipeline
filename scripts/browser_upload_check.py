"""Upload a large file from a real browser straight to the store and measure the cost.

Run against a running stack (`make up`):
    uv run --with playwright --with boto3 --with numpy --with requests \\
        python scripts/browser_upload_check.py 300

Chromium runs a page on the frontend's origin, takes a pattern file from disk through a file
input (as a user would; an in-memory Blob of a gigabyte is not what a browser upload is) and PUTs
it to a presigned URL from `POST /uploads`. The script then checks the object's size and MD5 (the
ETag) and reports how much memory the frontend container grew by, which should be nothing: the
bytes never reach it.
"""

import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
import numpy as np
import requests
from botocore.client import Config
from playwright.sync_api import sync_playwright

MB = 1 << 20
ORIGIN, API, STORE = "http://localhost:8080", "http://localhost:8000", "http://localhost:8333"


def frontend_mem_mb() -> float:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "geo-pipeline-frontend-1"],
        capture_output=True,
        text=True,
        check=True,
    )
    used = out.stdout.split("/")[0].strip()
    if used.endswith("MiB"):
        return float(used.removesuffix("MiB"))
    return float(used.removesuffix("GiB")) * 1024


def main(size_mb: int) -> None:
    up = requests.post(f"{API}/uploads", headers={"X-API-Key": "dev-key-a"}, timeout=30).json()
    pattern = (np.arange(MB, dtype=np.uint32) % 251).astype(np.uint8).tobytes()
    md5 = hashlib.md5()
    tmp = (
        Path(tempfile.mkdtemp(dir=Path.home())) / "upload.bin"
    )  # the snap Chromium cannot read /tmp
    with tmp.open("wb") as f:
        for _ in range(size_mb):
            f.write(pattern)
            md5.update(pattern)
    expected = md5.hexdigest()
    before = frontend_mem_mb()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium-browser", args=["--no-sandbox"]
        )
        page = browser.new_page()
        page.goto(ORIGIN)
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input type=file id=f>')")
        page.set_input_files("#f", str(tmp))
        started = time.monotonic()
        status = page.evaluate(
            """async (url) => {
            const file = document.getElementById('f').files[0];
            const r = await fetch(url, {method: 'PUT', body: file});
            return r.status;
        }""",
            up["upload_url"],
        )
        seconds = time.monotonic() - started
        browser.close()
    tmp.unlink()
    tmp.parent.rmdir()
    after = frontend_mem_mb()
    s3 = boto3.client(
        "s3",
        endpoint_url=STORE,
        aws_access_key_id="dev",
        aws_secret_access_key="devdevdev",
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )
    head = s3.head_object(Bucket="imagery", Key=up["key"])
    print(f"PUT status {status} in {seconds:.1f}s ({size_mb / seconds:.0f} MB/s)")
    size_ok = head["ContentLength"] == size_mb * MB
    md5_ok = head["ETag"].strip('"') == expected
    print(f"size ok: {size_ok}, md5 ok: {md5_ok}")
    print(f"frontend memory {before:.0f} MiB -> {after:.0f} MiB (growth {after - before:+.0f} MiB)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
