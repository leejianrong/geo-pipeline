FROM geo-gdal:py3.12
# Security fixes released since the base image was built (re-scan with `make scan`).
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/slicer/main.py /app/main.py
CMD ["python", "/app/main.py"]
