# GDAL + CPython + numpy 2.x base image.
#   docker build --build-arg PY=3.12 -t geo-gdal:py3.12 base/
#   docker build --build-arg PY=3.13 -t geo-gdal:py3.13 base/
ARG PY=3.12
ARG DEBIAN=trixie

FROM python:${PY}-slim-${DEBIAN} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal-dev gdal-bin build-essential \
    && rm -rf /var/lib/apt/lists/*
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal C_INCLUDE_PATH=/usr/include/gdal
# GDAL's Python bindings must match the system libgdal version exactly.
RUN pip install --no-cache-dir "numpy>=2,<3" setuptools wheel \
    && pip wheel --no-cache-dir --no-build-isolation --wheel-dir /wheels \
        "GDAL==$(gdal-config --version)"

FROM python:${PY}-slim-${DEBIAN}
LABEL org.opencontainers.image.source="https://github.com/leejianrong/geo-pipeline" \
      org.opencontainers.image.description="GDAL + CPython + numpy 2.x" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update && apt-get install -y --no-install-recommends gdal-bin \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir "numpy>=2,<3" /wheels/*.whl && rm -rf /wheels
COPY verify.py /usr/local/bin/verify-gdal
CMD ["python", "/usr/local/bin/verify-gdal"]
