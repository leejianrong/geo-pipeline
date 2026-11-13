"""Smoke test: GDAL bindings work with numpy 2.x. Exits non-zero on failure."""
import sys

import numpy as np
from osgeo import gdal, gdal_array  # noqa: F401

gdal.UseExceptions()
assert int(np.__version__.split(".")[0]) >= 2, f"numpy {np.__version__} is not 2.x"

ds = gdal.GetDriverByName("MEM").Create("", 64, 64, 3, gdal.GDT_Byte)
ds.WriteArray(np.ones((3, 64, 64), dtype=np.uint8))
assert ds.ReadAsArray().sum() == 3 * 64 * 64
assert "/vsis3/" in gdal.GetFileSystemsPrefixes(), "no /vsis3/ (curl) support"
for drv in ("GTiff", "COG", "PNG"):
    assert gdal.GetDriverByName(drv), f"missing driver {drv}"

print(f"python {sys.version.split()[0]} numpy {np.__version__} gdal {gdal.__version__} OK")
