"""GDAL raster access for the slicer: validity, 8-bit stretch, tile reads."""

from dataclasses import dataclass

import numpy as np
from osgeo import gdal

from geopipe.messages import Window
from geopipe.stretch import apply_stretch, compute_stretch
from geopipe.tiling import has_data, windows

OVERVIEW_PX = 2048  # longest side of the low-res read used for stats and validity
gdal.UseExceptions()


@dataclass
class Raster:
    ds: gdal.Dataset
    bands: list
    stretch: list[tuple[float, float]] | None  # None for uint8, which is used as-is
    valid: np.ndarray  # low-res mask: True where any band holds data

    @property
    def width(self) -> int:
        return self.ds.RasterXSize

    @property
    def height(self) -> int:
        return self.ds.RasterYSize


def open_raster(path: str) -> Raster:
    ds = gdal.Open(path)
    if not ds.GetProjection():
        raise ValueError("raster has no CRS")
    # RGB from bands 1-3; anything with fewer bands is shown as grey from band 1.
    bands = [ds.GetRasterBand(b) for b in ((1, 2, 3) if ds.RasterCount >= 3 else (1, 1, 1))]
    scale = min(1.0, OVERVIEW_PX / max(ds.RasterXSize, ds.RasterYSize))
    ow, oh = max(1, round(ds.RasterXSize * scale)), max(1, round(ds.RasterYSize * scale))
    overview = np.stack([b.ReadAsArray(buf_xsize=ow, buf_ysize=oh) for b in bands])
    nodata = bands[0].GetNoDataValue()
    valid = (overview != nodata).any(axis=0) if nodata is not None else np.ones((oh, ow), bool)
    stretch = None if bands[0].DataType == gdal.GDT_Byte else compute_stretch(overview, valid)
    return Raster(ds, bands, stretch, valid)


def plan(raster: Raster, tile_size: int, overlap: float) -> list[Window]:
    """Tile windows that overlap valid data. An approximation at overview resolution."""
    return [w for w in windows(raster.width, raster.height, tile_size, overlap)
            if has_data(raster.valid, w, raster.width, raster.height)]


def read_tile(raster: Raster, win: Window) -> np.ndarray:
    """(h, w, 3) uint8 for a window; reads only that window."""
    tile = np.dstack([b.ReadAsArray(win.x_off, win.y_off, win.w, win.h) for b in raster.bands])
    return tile if raster.stretch is None else apply_stretch(tile, raster.stretch)
