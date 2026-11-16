"""The Inspector's data: one JSON object per tenant describing its latest finished job."""

MAX_INSPECT_BOXES = 200_000  # about 9 MB; a bigger raster needs per-tile files (GEO-85)


def build_inspect(job_id: str, meta: dict, per_tile: dict[int, dict], merged: list[dict],
                  cut: list[dict], missing: list[int]) -> dict:
    """What the Inspector draws: the cut, each tile's raw boxes (tile pixels), and for each
    merged box the raw boxes it stands for. Boxes are `[x1, y1, x2, y2, score, class_id]`."""
    drop = {(c["tile"], c["index"]) for c in cut}
    tiles = [{"idx": i, "window": per_tile[i]["window"],
              "raw": [[b["x1"], b["y1"], b["x2"], b["y2"], b["score"], b["class_id"]]
                      for b in per_tile[i]["boxes"]],
              "cut": [n for n in range(len(per_tile[i]["boxes"])) if (i, n) in drop]}
             for i in sorted(per_tile)]
    return {"schema_version": 1, "job_id": job_id, "raster": [meta["width"], meta["height"]],
            "params": {"overlap": meta["overlap"], "iou": meta["iou"]},
            "missing_tiles": missing, "tiles": tiles,
            "merged": [{"box": [b["x1"], b["y1"], b["x2"], b["y2"]], "score": b["score"],
                        "class_id": b["class_id"], "from": b["from"]} for b in merged]}


def inspect_key(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/inspect/latest.json"
