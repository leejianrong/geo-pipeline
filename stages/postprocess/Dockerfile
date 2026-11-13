FROM geo-gdal:py3.12
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/postprocess/main.py /app/main.py
CMD ["python", "/app/main.py"]
