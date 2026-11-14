"""Benchmark: run images through the real pipeline for each model and score the GeoJSON.

COCO images are wrapped as GeoTIFFs (arbitrary CRS, 1 m pixels) so the pixel boxes in the
output line up with COCO's ground truth; images that already are `.tif` (SpaceNet) are
uploaded as they are. Needs the `bench` extra (pycocotools, matplotlib); the
GeoTIFF writer needs GDAL, so run it in the tools container.
"""

import contextlib
import io
import json
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np

from .jobs import submit_job
from .messages import Options

BUCKET = "imagery"
TERMINAL = ("done", "incomplete", "error")


def write_geotiff(rgb: np.ndarray, path: str | Path) -> None:
    """Write an (h, w, 3) uint8 array as a georeferenced GeoTIFF (EPSG:32633, 1 m pixels)."""
    from osgeo import gdal, osr  # needs GDAL

    gdal.UseExceptions()
    h, w, _ = rgb.shape
    ds = gdal.GetDriverByName("GTiff").Create(str(path), w, h, 3, gdal.GDT_Byte,
                                              ["COMPRESS=DEFLATE"])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((500000.0, 1.0, 0.0, 5000000.0, 0.0, -1.0))
    for b in range(3):
        ds.GetRasterBand(b + 1).WriteArray(np.ascontiguousarray(rgb[:, :, b]))
    ds = None  # close: the file is incomplete on disk until the dataset is released


def pick_images(coco: dict, limit: int) -> list[dict]:
    """The first `limit` images (by id) that have at least one non-crowd annotation."""
    labelled = {a["image_id"] for a in coco["annotations"] if not a["iscrowd"]}
    return [i for i in sorted(coco["images"], key=lambda i: i["id"]) if i["id"] in labelled][:limit]


def fetch_image(info: dict, images_dir: Path, download: bool) -> Path:
    path = images_dir / info["file_name"]
    if not path.exists():
        if not download:
            raise FileNotFoundError(f"{path} is missing; pass --download to fetch it from COCO")
        images_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(info["coco_url"], path)
    return path


def run_model(r, s3, keys: dict[int, str], model: str, tenant: str, conf: float, iou: float,
              timeout: float = 120) -> dict[int, dict]:
    """One job per image, sequentially, so latency is the pipeline's own and not queueing."""
    out = {}
    for image_id, key in keys.items():
        started = time.monotonic()
        job_id = submit_job(r, s3, BUCKET, key, tenant,
                            Options(model=model, conf=conf, iou=iou, overlap=0))
        while (state := r.hget(f"job:{job_id}:status", "state")) not in TERMINAL:
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"{model} job for image {image_id} timed out")
            time.sleep(0.05)
        latency = time.monotonic() - started
        status = r.hgetall(f"job:{job_id}:status")
        if state != "done":
            raise RuntimeError(f"{model} job for image {image_id} ended {state}: "
                               f"{status.get('error')}")
        bucket, out_key = status["output_uri"].removeprefix("s3://").split("/", 1)
        features = json.loads(s3.get_object(Bucket=bucket, Key=out_key)["Body"].read())["features"]
        out[image_id] = {"latency": latency, "detect_seconds": float(status["detect_seconds"]),
                         "features": features}
    return out


def to_coco_detections(image_id: int, features: list[dict], name_to_id: dict[str, int]) -> list:
    dets = []
    for f in features:
        p = f["properties"]
        x1, y1, x2, y2 = p["pixel_bbox"]
        if p["class_name"] in name_to_id:
            dets.append({"image_id": image_id, "category_id": name_to_id[p["class_name"]],
                         "bbox": [x1, y1, x2 - x1, y2 - y1], "score": p["score"]})
    return dets


def _iou_matrix(a: np.ndarray, b: np.ndarray, b_crowd: np.ndarray | None = None) -> np.ndarray:
    """IoU between xywh boxes; for crowd ground truth, intersection over the detection area."""
    ax1, ay1, ax2, ay2 = a[:, 0], a[:, 1], a[:, 0] + a[:, 2], a[:, 1] + a[:, 3]
    bx1, by1, bx2, by2 = b[:, 0], b[:, 1], b[:, 0] + b[:, 2], b[:, 1] + b[:, 3]
    w = np.clip(np.minimum(ax2[:, None], bx2) - np.maximum(ax1[:, None], bx1), 0, None)
    h = np.clip(np.minimum(ay2[:, None], by2) - np.maximum(ay1[:, None], by1), 0, None)
    inter = w * h
    area_a, area_b = (a[:, 2] * a[:, 3])[:, None], (b[:, 2] * b[:, 3])[None, :]
    union = area_a + area_b - inter
    if b_crowd is not None:
        union = np.where(b_crowd[None, :], area_a, union)
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def match_at_iou(gt: dict, dets: list[dict], image_ids: list[int], iou_thr: float = 0.5,
                 min_score: float = 0.5) -> dict:
    """Greedy per-image, per-class matching of detections (best score first) to ground truth.

    Crowd regions are not counted as ground truth, and detections that fall in one are ignored
    (neither true nor false positives), as in COCO's own evaluation.
    """
    tp = fp = fn = 0
    ious: list[float] = []
    for image_id in image_ids:
        gts = [a for a in gt["annotations"] if a["image_id"] == image_id]
        mine = [d for d in dets if d["image_id"] == image_id and d["score"] >= min_score]
        for cid in {a["category_id"] for a in gts} | {d["category_id"] for d in mine}:
            g = [a for a in gts if a["category_id"] == cid]
            solid = np.array([a["bbox"] for a in g if not a["iscrowd"]], float).reshape(-1, 4)
            crowd = np.array([a["bbox"] for a in g if a["iscrowd"]], float).reshape(-1, 4)
            d = sorted((x for x in mine if x["category_id"] == cid), key=lambda x: -x["score"])
            taken = np.zeros(len(solid), bool)
            for det in d:
                box = np.array([det["bbox"]], float)
                best, best_i = 0.0, -1
                if len(solid):
                    row = _iou_matrix(box, solid)[0]
                    row[taken] = -1
                    best_i = int(row.argmax())
                    best = float(row[best_i])
                if best >= iou_thr:
                    taken[best_i] = True
                    tp += 1
                    ious.append(best)
                elif len(crowd) and _iou_matrix(box, crowd, np.ones(len(crowd), bool)).max() >= 0.5:
                    continue  # inside a crowd region
                else:
                    fp += 1
            fn += int((~taken).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
            "ious": ious}


def evaluate(gt: dict, dets: list[dict], image_ids: list[int]) -> dict:
    """COCO mAP via pycocotools, plus the precision-recall curve at IoU 0.5."""
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    with contextlib.redirect_stdout(io.StringIO()):  # pycocotools prints progress
        coco = COCO()
        coco.dataset = gt
        coco.createIndex()
        if not dets:
            return {"map": 0.0, "map50": 0.0, "ar100": 0.0, "pr_recall": [], "pr_precision": []}
        ev = COCOeval(coco, coco.loadRes(dets), "bbox")
        ev.params.imgIds = image_ids
        ev.evaluate()
        ev.accumulate()
        ev.summarize()
    prec = ev.eval["precision"][0, :, :, 0, 2]  # IoU 0.5, all areas, 100 detections
    valid = (prec > -1).any(axis=0)  # classes with ground truth in this subset
    curve = np.where(prec[:, valid] > -1, prec[:, valid], np.nan)
    return {"map": float(ev.stats[0]), "map50": float(ev.stats[1]), "ar100": float(ev.stats[8]),
            "pr_recall": [float(x) for x in ev.params.recThrs],
            "pr_precision": [float(x) for x in np.nan_to_num(np.nanmean(curve, axis=1))]}


def score_model(gt: dict, name_to_id: dict, runs: dict[int, dict], image_ids: list[int]) -> dict:
    """All metrics for one model's run over `image_ids`."""
    dets = [d for i in image_ids for d in to_coco_detections(i, runs[i]["features"], name_to_id)]
    m = evaluate(gt, dets, image_ids)
    matched = match_at_iou(gt, dets, image_ids)
    latencies = [runs[i]["latency"] for i in image_ids]
    return {**m, **{k: matched[k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
            "ious": matched["ious"], "detections": len(dets), "latencies": latencies,
            "median_latency": float(np.median(latencies)),
            "mean_detect_seconds": float(np.mean([runs[i]["detect_seconds"] for i in image_ids]))}


COCO_CAVEAT = ("COCO shows everyday objects. These numbers do not predict performance on "
               "overhead imagery.")


def write_report(results: dict[str, dict], n_images: int, conf: float, out: Path,
                 title: str | None = None, caveat: str = COCO_CAVEAT) -> Path:
    """Markdown report, JSON of all numbers, and PNG plots."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4.5))
    for name, m in results.items():
        ax.plot(m["pr_recall"], m["pr_precision"], label=f"{name} (mAP@.5 {m['map50']:.3f})")
    ax.set(xlabel="recall", ylabel="precision", xlim=(0, 1), ylim=(0, 1.02),
           title="Precision-recall at IoU 0.5 (mean over classes)")
    ax.legend()
    fig.savefig(out / "pr_curves.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for name, m in results.items():
        axes[0].hist(m["ious"], bins=20, range=(0.5, 1), alpha=0.6, label=name)
    axes[0].set(xlabel="IoU of matched boxes (score >= 0.5)", ylabel="boxes",
                title="Box tightness")
    axes[0].legend()
    axes[1].boxplot([m["latencies"] for m in results.values()], tick_labels=list(results))
    axes[1].set(ylabel="seconds per image", title="End-to-end latency (submit to done)")
    fig.tight_layout()
    fig.savefig(out / "latency_iou.png", dpi=130)
    plt.close(fig)

    rows = "\n".join(
        f"| {n} | {m['map']:.3f} | {m['map50']:.3f} | {m['ar100']:.3f} | {m['precision']:.3f} | "
        f"{m['recall']:.3f} | {m['f1']:.3f} | {m['median_latency']:.2f} | "
        f"{m['mean_detect_seconds']:.2f} | {m['detections']} |" for n, m in results.items())
    header = ("| model | mAP@[.5:.95] | mAP@.5 | AR@100 | P@.5 | R@.5 | F1@.5 "
              "| median latency s/img | mean detect s/img | detections |")
    (out / "report.md").write_text(f"""# {title or f"COCO benchmark: {n_images} val2017 images"}

{header}
|---|---|---|---|---|---|---|---|---|---|
{rows}

- Detections were collected at confidence >= {conf} (needed for mAP). P, R and F1 use detections
  with score >= 0.5 and IoU >= 0.5.
- Latency is client-observed, from submit to done, through the whole pipeline (S3, queue, slicer,
  detector, postprocess), one image at a time. "detect" is the model's own time.
- {caveat}

![precision-recall](pr_curves.png)
![iou and latency](latency_iou.png)
""")
    bulky = ("ious", "latencies", "pr_recall", "pr_precision")
    slim = {n: {k: v for k, v in m.items() if k not in bulky} for n, m in results.items()}
    (out / "results.json").write_text(json.dumps(slim, indent=2))
    return out / "report.md"


def save_run(r, s3, model: str, dataset_id: str, split: str, scored: dict, dets: list[dict],
             conf: float, iou: float) -> str:
    """Store one scored model run in the registry (S2); returns the run id."""
    import time

    from . import registry

    run = registry.RunManifest(
        id=f"run-{uuid.uuid4().hex[:12]}", model_id=model, dataset_id=dataset_id, split=split,
        scorer="pipeline", conf=conf, iou=iou, created_at=time.time(),
        metrics={k: float(scored[k]) for k in registry.METRIC_KEYS})
    registry.write_run(s3, r, BUCKET, run, scored, dets)
    return run.id


def run_benchmark(r, s3, coco: dict, models: list[str], images_dir: Path, out: Path, limit: int,
                  conf: float = 0.05, iou: float = 0.65, download: bool = False,
                  register: tuple[str, str] | None = None, **report) -> dict:
    """Upload the images once, run every model over them, score and write the report.

    `register=(dataset_id, split)` also stores each model's run, with its predictions, in the
    registry so the studio can show it."""
    import tempfile

    from PIL import Image

    from .store import ensure_bucket

    infos = pick_images(coco, limit)
    ensure_bucket(s3, BUCKET)
    keys = {}
    with tempfile.TemporaryDirectory() as tmp:
        for info in infos:
            path = fetch_image(info, images_dir, download)
            if path.suffix == ".tif":
                tif = path
            else:
                tif = Path(tmp) / f"{info['id']}.tif"
                write_geotiff(np.array(Image.open(path).convert("RGB")), tif)
            keys[info["id"]] = f"bench/{info['id']}.tif"
            s3.upload_file(str(tif), BUCKET, keys[info["id"]])
    name_to_id = {c["name"]: c["id"] for c in coco["categories"]}
    # A fresh tenant per run: resubmitting identical options would otherwise be a no-op.
    tenant = f"bench-{uuid.uuid4().hex[:8]}"
    results = {}
    for model in models:
        runs = run_model(r, s3, keys, model, tenant, conf, iou)
        results[model] = score_model(coco, name_to_id, runs, list(keys))
        if register:
            dets = [d for i in keys for d in to_coco_detections(i, runs[i]["features"], name_to_id)]
            save_run(r, s3, model, *register, results[model], dets, conf, iou)
    write_report(results, len(infos), conf, out, **report)
    return results
