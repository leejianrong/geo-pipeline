FROM python:3.12-slim-trixie
# Security fixes released since the base image was built (re-scan with `make scan`).
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U setuptools wheel
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe onnx onnxruntime pycocotools
COPY stages/trainer/main.py /app/main.py
CMD ["python", "/app/main.py"]
