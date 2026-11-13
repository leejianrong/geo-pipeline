FROM geo-gdal:py3.12
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/slicer/main.py stages/slicer/raster.py /app/
CMD ["python", "/app/main.py"]
