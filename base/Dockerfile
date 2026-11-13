# GDAL (built from source) + CPython + numpy 2.x base image.
#   base/build.sh 3.12 3.12      # GDAL 3.12.x on Python 3.12, from base/matrix.json
# GDAL_VERSION must be a release in https://download.osgeo.org/gdal/ that also exists on PyPI as
# the `GDAL` Python bindings: the bindings have to match libgdal exactly.
ARG PY=3.12
ARG DEBIAN=trixie

# 1. libgdal. Independent of the Python version, so both Python variants share this stage.
FROM debian:${DEBIAN}-slim AS gdal
ARG GDAL_VERSION=3.12.4
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build pkg-config curl ca-certificates xz-utils \
        libproj-dev libgeos-dev libcurl4-openssl-dev libtiff-dev libgeotiff-dev libpng-dev \
        libjpeg-dev libwebp-dev libopenjp2-7-dev libzstd-dev libsqlite3-dev libexpat1-dev \
        libxml2-dev libssl-dev zlib1g-dev libdeflate-dev libnetcdf-dev libhdf5-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# Installed under /opt/gdal so it cannot mix with the Python that lives in /usr/local.
RUN curl -fsSL "https://download.osgeo.org/gdal/${GDAL_VERSION}/gdal-${GDAL_VERSION}.tar.xz" \
        | tar -xJ -C /tmp \
    && cmake -S "/tmp/gdal-${GDAL_VERSION}" -B /tmp/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/gdal \
        -DBUILD_PYTHON_BINDINGS=OFF -DBUILD_JAVA_BINDINGS=OFF -DBUILD_CSHARP_BINDINGS=OFF \
        -DBUILD_TESTING=OFF \
    && cmake --build /tmp/build \
    && cmake --install /tmp/build \
    && rm -rf /tmp/build "/tmp/gdal-${GDAL_VERSION}"
# Debian packages that provide the shared libraries libgdal links against.
# readlink -f: ldd reports /lib/... but dpkg records /usr/lib/... (merged-/usr).
RUN for lib in $(ldd /opt/gdal/lib/libgdal.so | awk '/=> \//{print $3}'); do \
        dpkg -S "$(readlink -f "$lib")"; done 2>/dev/null \
        | cut -d: -f1 | sort -u > /opt/gdal/runtime-packages.txt \
    && test -s /opt/gdal/runtime-packages.txt

# 2. Python bindings, compiled against that libgdal and numpy 2.
FROM python:${PY}-slim-${DEBIAN} AS bindings
ARG GDAL_VERSION=3.12.4
COPY --from=gdal /opt/gdal /opt/gdal
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential $(cat /opt/gdal/runtime-packages.txt) \
    && rm -rf /var/lib/apt/lists/* \
    && echo /opt/gdal/lib > /etc/ld.so.conf.d/gdal.conf && ldconfig
ENV PATH=/opt/gdal/bin:$PATH C_INCLUDE_PATH=/opt/gdal/include CPLUS_INCLUDE_PATH=/opt/gdal/include
RUN pip install --no-cache-dir "numpy>=2,<3" setuptools wheel \
    && pip wheel --no-cache-dir --no-build-isolation --wheel-dir /wheels "GDAL==${GDAL_VERSION}"

# 3. Runtime image: no compilers needed at run time.
FROM python:${PY}-slim-${DEBIAN}
ARG GDAL_VERSION=3.12.4
LABEL org.opencontainers.image.source="https://github.com/leejianrong/geo-pipeline" \
      org.opencontainers.image.description="GDAL ${GDAL_VERSION} + CPython + numpy 2.x" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=gdal /opt/gdal /opt/gdal
COPY --from=bindings /wheels /wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends $(cat /opt/gdal/runtime-packages.txt) \
    && rm -rf /var/lib/apt/lists/* \
    && echo /opt/gdal/lib > /etc/ld.so.conf.d/gdal.conf && ldconfig \
    && if ldd /opt/gdal/lib/libgdal.so | grep "not found"; then exit 1; fi \
    && pip install --no-cache-dir "numpy>=2,<3" /wheels/*.whl && rm -rf /wheels
ENV PATH=/opt/gdal/bin:$PATH GDAL_DATA=/opt/gdal/share/gdal
COPY verify.py /usr/local/bin/verify-gdal
CMD ["python", "/usr/local/bin/verify-gdal"]
