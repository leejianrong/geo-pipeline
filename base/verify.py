"""Runtime check that GDAL's Python bindings work with numpy 2.x. Exits non-zero on failure.

Exercises the numpy C-API paths (array <-> dataset copies, OpenArray, dtype mapping) rather
than only importing, since an ABI mismatch can import fine and fail on first use.
"""

import sys

import numpy as np
from osgeo import gdal, gdal_array

gdal.UseExceptions()
assert int(np.__version__.split(".")[0]) >= 2, f"numpy {np.__version__} is not 2.x"

for dtype in (np.uint8, np.uint16, np.int16, np.float32, np.float64, np.complex64):
    src = (np.arange(3 * 16 * 16).reshape(3, 16, 16) % 200).astype(dtype)
    gdal_type = gdal_array.NumericTypeCodeToGDALTypeCode(dtype)
    ds = gdal.GetDriverByName("MEM").Create("", 16, 16, 3, gdal_type)
    ds.WriteArray(src)
    assert np.array_equal(ds.ReadAsArray(), src), f"round trip failed for {np.dtype(dtype)}"

view = np.zeros((8, 8), np.uint8)
ds = gdal_array.OpenArray(view)  # shares memory with the numpy array, in both directions
ds.GetRasterBand(1).Fill(7)
ds.FlushCache()
assert view.max() == 7, "GDAL writes are not visible through the numpy array"
view[0, 0] = 99
assert ds.ReadAsArray()[0, 0] == 99, "numpy writes are not visible through GDAL"

windowed = gdal.GetDriverByName("MEM").Create("", 64, 64, 1, gdal.GDT_Byte)
windowed.WriteArray(np.full((10, 10), 9, np.uint8), 5, 5)
assert windowed.GetRasterBand(1).ReadAsArray(5, 5, 10, 10).min() == 9

assert "/vsis3/" in gdal.GetFileSystemsPrefixes(), "no /vsis3/ (curl) support"
for drv in ("GTiff", "COG", "PNG"):
    assert gdal.GetDriverByName(drv), f"missing driver {drv}"

print(f"python {sys.version.split()[0]} numpy {np.__version__} gdal {gdal.__version__} OK")
