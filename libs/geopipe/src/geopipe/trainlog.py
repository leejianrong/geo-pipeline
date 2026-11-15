"""Training progress from the two trainers' own output (ADR-0016), as plain dicts with the keys
`epoch`, `epochs`, `loss` and `val_ap` (AP at IoU 0.5, the number the ADR-0014 bar uses).

YOLOX prints a line per few iterations and the COCO evaluation block after an evaluation epoch;
RF-DETR writes `metrics.csv` (one row per logging step, validation rows carry `val/mAP_50`).
"""

import csv
import io
import re

YOLOX_ITER = re.compile(r"epoch: (\d+)/(\d+), iter: \d+/\d+,.*?total_loss: ([\d.]+)")
YOLOX_AP50 = re.compile(r"Average Precision\s+\(AP\) @\[ IoU=0\.50\s+\| area=\s*all \| "
                        r"maxDets=100 \] = (-?[\d.]+)")


class YoloxLog:
    """Feed it YOLOX's output line by line; it returns an update when a line carries one."""

    def __init__(self) -> None:
        self.epoch = 0

    def feed(self, line: str) -> dict | None:
        if m := YOLOX_ITER.search(line):
            self.epoch = int(m[1])
            return {"epoch": self.epoch, "epochs": int(m[2]), "loss": float(m[3])}
        if (m := YOLOX_AP50.search(line)) and self.epoch:
            return {"epoch": self.epoch, "val_ap": max(float(m[1]), 0.0)}  # -1 means "no data"
        return None


def rfdetr_progress(metrics_csv: str) -> list[dict]:
    """One update per epoch seen in RF-DETR's `metrics.csv`, oldest first. An epoch's loss is its
    last training row's, its AP its validation row's; a missing value is left out."""
    by_epoch: dict[int, dict] = {}
    for row in csv.DictReader(io.StringIO(metrics_csv)):
        try:
            epoch = int(row["epoch"]) + 1  # RF-DETR counts from 0; the status counts from 1
        except (KeyError, TypeError, ValueError):
            continue
        entry = by_epoch.setdefault(epoch, {"epoch": epoch})
        for column, key in (("train/loss", "loss"), ("val/mAP_50", "val_ap")):
            if row.get(column):
                entry[key] = float(row[column])
    return [by_epoch[e] for e in sorted(by_epoch)]
