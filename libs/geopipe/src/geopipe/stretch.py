"""8-bit conversion for non-uint8 rasters. One stretch per job so every tile maps the same way."""

import numpy as np


def rgb_bands(band_count: int) -> tuple[int, int, int]:
    """1-based bands shown as RGB: bands 1-3, or band 1 as grey when there are fewer than 3."""
    return (1, 2, 3) if band_count >= 3 else (1, 1, 1)


def compute_stretch(overview: np.ndarray, valid: np.ndarray) -> list[tuple[float, float]]:
    """Per-band 2nd-98th percentile over valid pixels of a (3, h, w) overview."""
    out = []
    for band in overview:
        vals = band[valid]
        lo, hi = np.percentile(vals, (2, 98)) if vals.size else (0.0, 1.0)
        out.append((float(lo), float(max(hi, lo + 1))))
    return out


def apply_stretch(tile: np.ndarray, stretch: list[tuple[float, float]]) -> np.ndarray:
    """(h, w, 3) any dtype -> (h, w, 3) uint8."""
    lo = np.array([s[0] for s in stretch], dtype=np.float32)
    hi = np.array([s[1] for s in stretch], dtype=np.float32)
    scaled = (np.nan_to_num(tile.astype(np.float32)) - lo) / (hi - lo)
    return (scaled.clip(0, 1) * 255).astype(np.uint8)
