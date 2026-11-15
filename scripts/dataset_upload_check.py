"""Drive the real Train tab in Chromium: choose a big file, watch it reach the store.

Run against a running stack (`make up`):
    uv run --with playwright --with boto3 python scripts/dataset_upload_check.py 300

The file goes through the page's file input (as a user would) and the `DirectUpload`
component PUTs it to the store. The script then checks the object's size and the frontend
container's memory, which should not grow: the bytes never reach it.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.client import Config
from playwright.sync_api import expect, sync_playwright

MB = 1 << 20
ORIGIN, STORE = "http://localhost:8080", "http://localhost:8333"


def frontend_mem_mb() -> float:
    out = (
        subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.MemUsage}}",
                "geo-pipeline-frontend-1",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.split("/")[0]
        .strip()
    )
    if out.endswith("MiB"):
        return float(out.removesuffix("MiB"))
    return float(out.removesuffix("GiB")) * 1024


def main(size_mb: int) -> None:
    tmp = Path(tempfile.mkdtemp(dir=Path.home())) / "dataset.zip"  # snap Chromium cannot read /tmp
    chunk = bytes(range(251)) * (MB // 251 + 1)
    with tmp.open("wb") as f:
        for _ in range(size_mb):
            f.write(chunk[:MB])
    before = frontend_mem_mb()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium-browser", args=["--no-sandbox"]
        )
        page = browser.new_page()
        page.goto(ORIGIN)
        page.get_by_label("API key").fill("dev-key-a")
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("tab", name="Train").click()
        page.get_by_label("Dataset zip").set_input_files(str(tmp))
        bar = page.locator(".upload", has=page.get_by_label("Dataset zip")).locator("progress")
        seen = set()
        for _ in range(600):
            value = bar.get_attribute("value")
            if value:
                seen.add(int(value))
            if page.get_by_text("Uploaded dataset.zip").count() > 1:
                break
            page.wait_for_timeout(100)
        expect(page.get_by_text("Uploaded dataset.zip").first).to_be_visible()
        print(f"progress values seen: {len(seen)} (max {max(seen, default=0)})")
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
    objs = s3.list_objects_v2(Bucket="imagery", Prefix="tenants/acme/uploads/datasets/")["Contents"]
    newest = max(objs, key=lambda o: o["LastModified"])
    print(
        f"stored {newest['Key']}: {newest['Size']} bytes, expected {size_mb * MB}, "
        f"ok: {newest['Size'] == size_mb * MB}"
    )
    print(f"frontend memory {before:.0f} MiB -> {after:.0f} MiB (growth {after - before:+.0f} MiB)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
