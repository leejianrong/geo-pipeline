"""SpaceNet loader on a tiny EPSG:4326 tile, like the real ones (GDAL needed)."""

import json

import pytest
from osgeo import gdal, osr

from geopipe import spacenet

GT = (32.0, 0.0000027, 0.0, 15.0, 0.0, -0.0000027)


def test_tile_boxes_and_coco_round_trip_through_the_geotransform(tmp_path):
    (tmp_path / "PS-RGB").mkdir()
    (tmp_path / "geojson").mkdir()
    ds = gdal.GetDriverByName("GTiff").Create(str(tmp_path / "PS-RGB/img7.tif"), 100, 80, 3,
                                              gdal.GDT_UInt16)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform(GT)
    ds = None
    ring = [[GT[0] + x * GT[1], GT[3] + y * GT[5], 0.0] for x, y in ((10, 20), (40, 20), (40, 50),
                                                                     (10, 50))]
    feats = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}},
             {"type": "Feature", "geometry": None}]  # SpaceNet marks empty tiles like this
    (tmp_path / "geojson/img7.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}))

    assert spacenet.tile_ids(tmp_path) == [7]
    coco = spacenet.coco_for(tmp_path, [7], lambda i: f"img{i}.tif")
    assert coco["images"][0]["width"] == 100 and coco["images"][0]["height"] == 80
    assert coco["annotations"][0]["bbox"] == pytest.approx([10, 20, 30, 30], abs=1e-3)
    assert len(coco["annotations"]) == 1
