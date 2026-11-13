FROM geo-gdal:py3.12
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe pytest
WORKDIR /repo
