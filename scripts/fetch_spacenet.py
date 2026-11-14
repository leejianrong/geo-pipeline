"""Download a subset of SpaceNet 2 (CC BY-SA 4.0) PS-RGB tiles and building footprints.

Anonymous access to the public bucket `spacenet-dataset`; nothing is committed (ADR-0010).
Usage: uv run --with boto3 python scripts/fetch_spacenet.py [--aoi AOI_5_Khartoum] [--count 300]
"""

import argparse
import re
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

BUCKET = "spacenet-dataset"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--aoi", default="AOI_5_Khartoum")
    p.add_argument("--count", type=int, default=300)
    p.add_argument("--out", default="data/spacenet")
    a = p.parse_args()
    s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
    base = f"spacenet/SN2_buildings/train/{a.aoi}/"
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=base + "PS-RGB/") for o in page.get("Contents", [])]
    # numeric order, so the subset is the same on every run
    keys.sort(key=lambda k: int(re.search(r"_img(\d+)\.tif$", k).group(1)))
    out = Path(a.out) / a.aoi
    for key in keys[:a.count]:
        stem = re.search(r"PS-RGB_(img\d+)\.tif$", key).group(1)
        for src, dst in ((key, out / "PS-RGB" / f"{stem}.tif"),
                         (key.replace("PS-RGB/", "geojson_buildings/").replace(
                             "PS-RGB_", "geojson_buildings_").replace(".tif", ".geojson"),
                          out / "geojson" / f"{stem}.geojson")):
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(BUCKET, src, str(dst))
    print(f"{len(list((out / 'PS-RGB').glob('*.tif')))} tiles in {out}")


if __name__ == "__main__":
    main()
