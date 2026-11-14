FROM python:3.12-slim-trixie
# CPU-only torch keeps the default image far smaller than the CUDA wheels. compose.gpu.yaml
# builds the GPU variant with TORCH_INDEX=.../cu128 and ORT=onnxruntime-gpu.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG ORT=onnxruntime
RUN pip install --no-cache-dir torch torchvision --index-url $TORCH_INDEX
RUN pip install --no-cache-dir rfdetr $ORT
# Bake the pretrained weights in so the container runs offline.
RUN python -c "from rfdetr import RFDETRSmall; RFDETRSmall()"
# YOLOX-s, Apache-2.0, from the official release (raw head output; see yolox_onnx.py).
ADD --checksum=sha256:c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063 \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx /models/yolox_s.onnx
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/detector/backends.py stages/detector/yolox_onnx.py stages/detector/main.py /app/
CMD ["python", "/app/main.py"]
