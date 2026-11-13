# Inspecting results in QGIS

1. Download the raster and the result: `geopipe download s3://imagery/<key>.tif /data/out/in.tif` and `geopipe download s3://imagery/tenants/local/results/<job>.geojson /data/out/result.geojson` (files appear in `./data/out/` on the host).
2. In QGIS: **Layer > Add Layer > Add Raster Layer** for the `.tif`, then **Add Vector Layer** for the `.geojson`. The GeoJSON is in EPSG:4326; QGIS reprojects on the fly.
3. Style the vector layer with **Categorized** on `class_name`, fill set to transparent so the imagery shows through. Use `score` in a **Rule-based** style (for example `"score" >= 0.5`) to hide weak detections, and the label `"class_name"` to see classes.
4. `pixel_bbox` holds each box in the source raster's pixels, which is handy for cross-checking against `gdalinfo` or the PNG plots from `geopipe viz`.
