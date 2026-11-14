FROM geo-gdal:py3.12
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir "/src/geopipe[viz,bench,api]" "indah==0.13.0" httpx pytest pytest-asyncio
WORKDIR /repo
