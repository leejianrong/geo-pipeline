"""Stand-in for your application: GDAL, numpy and a compiled package all work without a shell."""
import sys

import numpy as np
import orjson
from osgeo import gdal, osr

gdal.UseExceptions()
srs = osr.SpatialReference()
srs.ImportFromEPSG(4326)  # needs PROJ_DATA
ds = gdal.GetDriverByName("MEM").Create("", 4, 4, 1, gdal.GDT_Float32)
ds.WriteArray(np.arange(16, dtype="f4").reshape(4, 4))
assert ds.ReadAsArray().sum() == 120
print(sys.version.split()[0], "numpy", np.__version__, "gdal", gdal.__version__,
      orjson.dumps({"epsg": srs.GetAuthorityCode(None)}).decode())
