"""SpaceNet 2 buildings (CC BY-SA 4.0, never committed): footprints to boxes, COCO files.

Tiles are 16-bit PS-RGB GeoTIFFs in EPSG:4326; footprints are GeoJSON polygons in lon/lat, so
a vertex maps to a pixel through the inverse geotransform alone. `prepare` writes a COCO
dataset: 8-bit PNGs (stretched like the pipeline does) for training and the original GeoTIFFs'
boxes for the held-out benchmark. Needs GDAL, so it runs in a GDAL container.
"""

import json
import re
from pathlib import Path

CATEGORIES = [{"id": 1, "name": "building"}]
MIN_SIDE = 2.0  # pixels; slivers at tile edges are not detectable objects


def polygon_bbox(rings: list, gt: tuple, width: int, height: int) -> list[float] | None:
    """Outer ring of lon/lat vertices -> [x, y, w, h] in pixels, clipped to the tile.

    `gt` is a north-up GDAL geotransform. Returns None for an empty polygon or one whose
    clipped box is thinner than MIN_SIDE.
    """
    if not rings or not rings[0]:
        return None
    xs = [(p[0] - gt[0]) / gt[1] for p in rings[0]]
    ys = [(p[1] - gt[3]) / gt[5] for p in rings[0]]
    x1, x2 = max(min(xs), 0.0), min(max(xs), float(width))
    y1, y2 = max(min(ys), 0.0), min(max(ys), float(height))
    if x2 - x1 < MIN_SIDE or y2 - y1 < MIN_SIDE:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def tile_boxes(tif: Path, geojson: Path) -> tuple[list[list[float]], int, int]:
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(str(tif))
    gt, w, h = ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize
    ds = None
    feats = json.loads(geojson.read_text())["features"]
    boxes = [polygon_bbox(f["geometry"]["coordinates"], gt, w, h)
             for f in feats if f.get("geometry") and f["geometry"]["type"] == "Polygon"]
    return [b for b in boxes if b], w, h


def tile_ids(root: Path) -> list[int]:
    """Tile numbers that have both an image and footprints, in numeric order."""
    ids = (int(re.search(r"img(\d+)\.tif$", p.name).group(1))
           for p in (root / "PS-RGB").glob("*.tif"))
    return sorted(i for i in ids if (root / "geojson" / f"img{i}.geojson").exists())


def coco_for(root: Path, ids: list[int], file_name) -> dict:
    """COCO dict for tiles `ids`; `file_name(i)` gives each image's name in that dataset."""
    images, anns = [], []
    for i in ids:
        boxes, w, h = tile_boxes(root / "PS-RGB" / f"img{i}.tif",
                                 root / "geojson" / f"img{i}.geojson")
        images.append({"id": i, "file_name": file_name(i), "width": w, "height": h})
        anns += [{"id": i * 10_000 + k, "image_id": i, "category_id": 1, "bbox": b,
                  "area": b[2] * b[3], "iscrowd": 0} for k, b in enumerate(boxes)]
    return {"images": images, "annotations": anns, "categories": CATEGORIES}


def split(ids: list[int], valid: int = 30, test: int = 30) -> dict[str, list[int]]:
    """Contiguous blocks of tile numbers, so held-out tiles are not neighbours of training ones."""
    return {"train": ids[:-(valid + test)], "valid": ids[-(valid + test):-test],
            "test": ids[-test:]}


def prepare(root: Path, out: Path) -> dict[str, int]:
    """Write `out/{train,valid,test}/` (PNG + `_annotations.coco.json`, the layout RF-DETR trains
    on and the studio registers) and `out/test.json` (boxes for the GeoTIFFs in `root/PS-RGB`,
    for `geopipe bench`)."""
    from PIL import Image

    from .messages import Window
    from .raster import open_raster, read_tile

    parts = split(tile_ids(root))
    for name in ("train", "valid", "test"):
        (out / name).mkdir(parents=True, exist_ok=True)
        for i in parts[name]:
            raster = open_raster(str(root / "PS-RGB" / f"img{i}.tif"))
            win = Window(x_off=0, y_off=0, w=raster.width, h=raster.height)
            Image.fromarray(read_tile(raster, win)).save(out / name / f"img{i}.png")
        coco = coco_for(root, parts[name], lambda i: f"img{i}.png")
        (out / name / "_annotations.coco.json").write_text(json.dumps(coco))
    (out / "test.json").write_text(json.dumps(coco_for(root, parts["test"],
                                                       lambda i: f"img{i}.tif")))
    return {k: len(v) for k, v in parts.items()}
