# Distroless runtime for GDAL + numpy 2 on Python 3.12 or 3.13, built on top of geo-gdal.
#   docker build --build-arg PY=3.13 -t my-app .            (run from this directory)
# The interpreter and its standard library are copied from the geo-gdal image into
# distroless/cc, so one recipe covers both versions and the Python is as current as the base.
ARG PY=3.13
ARG BASE=ghcr.io/leejianrong/geo-gdal:py${PY}

FROM ${BASE} AS build
ARG PY
COPY collect-libs.sh /usr/local/bin/
RUN pip install --no-cache-dir orjson                  # <- your requirements
RUN pip uninstall -y pip setuptools wheel
COPY your_app/ /app/
# Every shared library the interpreter, GDAL and the Python extension modules need.
RUN collect-libs.sh /libs /opt/gdal /usr/local/bin/python${PY} /usr/local/lib

FROM gcr.io/distroless/cc-debian13:nonroot
ARG PY
COPY --from=build /libs/ /
COPY --from=build /opt/gdal /opt/gdal
COPY --from=build /usr/share/proj /usr/share/proj
COPY --from=build /usr/local/bin/python${PY} /usr/local/bin/python
COPY --from=build /usr/local/lib/ /usr/local/lib/
COPY --from=build /app /app
ENV LD_LIBRARY_PATH=/usr/local/lib:/opt/gdal/lib GDAL_DATA=/opt/gdal/share/gdal PROJ_DATA=/usr/share/proj
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/python"]
CMD ["main.py"]
