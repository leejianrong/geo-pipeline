"""PNG plots for finished jobs: input overlay, tile grid, distributions, stage latency.

Needs matplotlib (the `viz` extra); `load_job` also needs GDAL to read the raster overview.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from .analysis import distributions, tile_counts, timings  # noqa: E402
from .tiling import has_data, windows  # noqa: E402

DPI = 130


@dataclass
class JobData:
    job_id: str
    status: dict
    meta: dict
    features: list[dict]
    overview: np.ndarray  # (h, w, 3) uint8: the input at low resolution
    valid: np.ndarray  # low-res mask, True where the raster holds data

    @property
    def scale(self) -> float:
        """Overview pixels per source pixel."""
        return self.overview.shape[1] / self.meta["width"]


def load_job(r, s3, job_id: str) -> JobData:
    """Gather a finished job's status, metadata, GeoJSON and a low-res view of its input."""
    from .raster import open_raster  # imports GDAL
    from .stretch import apply_stretch

    status = r.hgetall(f"job:{job_id}:status")
    if status.get("state") != "done":
        raise SystemExit(f"job {job_id} is not done (state: {status.get('state', 'unknown')})")
    meta = json.loads(r.get(f"job:{job_id}:meta"))
    bucket, key = status["output_uri"].removeprefix("s3://").split("/", 1)
    features = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())["features"]
    raster = open_raster(f"/vsis3/{meta['bucket']}/{meta['key']}")
    rgb = np.moveaxis(raster.overview, 0, -1)
    rgb = apply_stretch(rgb, raster.stretch) if raster.stretch else rgb.astype(np.uint8)
    return JobData(job_id, status, meta, features, rgb, raster.valid)


def _class_colours(names) -> dict:
    cmap = plt.get_cmap("tab10")
    return {n: cmap(i % 10) for i, n in enumerate(sorted(names))}


def _label(job: JobData) -> str:
    return f"{job.job_id[:8]} ({job.meta['model']})"


def plot_overlay(job: JobData, path: Path) -> None:
    """The input with every final detection drawn on it."""
    oh, ow, _ = job.overview.shape
    fig, ax = plt.subplots(figsize=(10, max(2.0, 10 * oh / ow)))
    ax.imshow(job.overview)
    colours = _class_colours({f["properties"]["class_name"] for f in job.features})
    for f in job.features:
        x1, y1, x2, y2 = (v * job.scale for v in f["properties"]["pixel_bbox"])
        ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, linewidth=1.2,
                               edgecolor=colours[f["properties"]["class_name"]]))
    if colours:
        ax.legend(handles=[Patch(fill=False, edgecolor=c, label=n) for n, c in colours.items()],
                  loc="upper right", fontsize=8)
    ax.set_axis_off()
    ax.set_title(f"{_label(job)}: {len(job.features)} detections", fontsize=10)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def plot_tilegrid(job: JobData, path: Path) -> None:
    """Tile windows shaded by detections per tile; tiles skipped for having no data are grey."""
    m = job.meta
    wins = windows(m["width"], m["height"], m["tile_size"], m["overlap"])
    kept = [has_data(job.valid, w, m["width"], m["height"]) for w in wins]
    counts = tile_counts(wins, job.features)
    norm = Normalize(vmin=0, vmax=max(1, max(counts)))
    cmap = plt.get_cmap("YlOrRd")
    oh, ow, _ = job.overview.shape
    fig, ax = plt.subplots(figsize=(10, max(2.0, 10 * oh / ow)))
    ax.imshow(job.overview)
    s = job.scale
    with_data = [(w, c) for w, c, k in zip(wins, counts, kept, strict=True) if k]
    no_data = [(w, 0) for w, k in zip(wins, kept, strict=True) if not k]
    for is_kept, rects in ((True, with_data), (False, no_data)):
        patches = [Rectangle((w.x_off * s, w.y_off * s), w.w * s, w.h * s) for w, _ in rects]
        if not patches:
            continue
        if is_kept:
            coll = PatchCollection(patches, facecolors=[cmap(norm(c)) for _, c in rects],
                                   edgecolors="black", linewidths=0.3, alpha=0.3)
        else:
            coll = PatchCollection(patches, facecolors="lightgrey", edgecolors="grey",
                                   hatch="//", linewidths=0.3, alpha=0.7)
        ax.add_collection(coll)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, label="detections per tile",
                 fraction=0.035)
    ax.set_axis_off()
    skipped = kept.count(False)
    ax.set_title(f"{_label(job)}: {len(wins)} tiles, {skipped} skipped (no data)", fontsize=10)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def plot_distributions(job: JobData, path: Path) -> None:
    """Score, box size and per-class count of the final detections."""
    d = distributions(job.features)
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    for ax, key, title, xlabel in ((axes[0], "scores", "Score", "confidence"),
                                   (axes[1], "sizes", "Box size", "sqrt(area), source pixels")):
        if d[key]:
            ax.hist(d[key], bins=20, color="#4c78a8")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("detections")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if d["classes"]:
        names = sorted(d["classes"], key=d["classes"].get, reverse=True)
        axes[2].barh(names, [d["classes"][n] for n in names], color="#4c78a8")
        axes[2].invert_yaxis()
    axes[2].set_title("Detections per class", fontsize=10)
    axes[2].set_xlabel("detections")
    axes[2].xaxis.set_major_locator(MaxNLocator(integer=True))
    if not job.features:
        for ax in axes:
            ax.text(0.5, 0.5, "no detections", ha="center", va="center", transform=ax.transAxes,
                    color="grey")
    fig.suptitle(_label(job), fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def plot_latency(jobs: list[JobData], path: Path) -> None:
    """Per-stage seconds and throughput, one bar group per job."""
    rows = [timings(j.status, j.meta) for j in jobs]
    labels = [_label(j) for j in jobs]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), gridspec_kw={"width_ratios": [2, 1, 1]})
    bottom = np.zeros(len(jobs))
    for stage, colour in (("slice", "#4c78a8"), ("detect", "#f58518"), ("finalise", "#54a24b")):
        vals = np.array([r[stage] for r in rows])
        axes[0].bar(labels, vals, bottom=bottom, label=stage, color=colour)
        bottom += vals
    walls = [r["wall"] for r in rows]
    if any(w is not None for w in walls):
        axes[0].scatter(labels, [w if w is not None else np.nan for w in walls], color="black",
                        zorder=3, marker="D", label="wall (incl. queue wait)")
    axes[0].set_ylabel("seconds")
    axes[0].set_title("Stage time (detect = summed tile inference time)", fontsize=9)
    axes[0].legend(fontsize=8)
    for ax, key, title in ((axes[1], "tiles_per_s", "Tiles / s (wall)"),
                           (axes[2], "mpix_per_s", "Megapixels / s (wall)")):
        ax.bar(labels, [r[key] or 0 for r in rows], color="#4c78a8")
        ax.set_title(title, fontsize=9)
    for ax in axes:
        ax.tick_params(axis="x", labelrotation=20, labelsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def write_plots(jobs: list[JobData], out: str | Path) -> Path:
    """Write <out>/<job_id>/{overlay,tilegrid,distributions}.png and <out>/latency.png."""
    out = Path(out)
    for job in jobs:
        d = out / job.job_id
        d.mkdir(parents=True, exist_ok=True)
        plot_overlay(job, d / "overlay.png")
        plot_tilegrid(job, d / "tilegrid.png")
        plot_distributions(job, d / "distributions.png")
    plot_latency(jobs, out / "latency.png")
    return out
