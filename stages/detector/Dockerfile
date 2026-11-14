FROM python:3.12-slim-trixie
# CPU-only torch keeps the default image far smaller than the CUDA wheels. compose.gpu.yaml
# builds the GPU variant with TORCH_INDEX=.../cu128 and ORT=onnxruntime-gpu.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG ORT=onnxruntime
RUN pip install --no-cache-dir torch torchvision --index-url $TORCH_INDEX
RUN pip install --no-cache-dir rfdetr $ORT
# Bake the pretrained weights in so the container runs offline.
RUN python -c "from rfdetr import RFDETRNano, RFDETRSmall; RFDETRSmall(); RFDETRNano()"
# YOLOX-s, -tiny and -nano, Apache-2.0, from the official release (raw head output; see
# yolox_onnx.py).
ADD --checksum=sha256:c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063 \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx /models/yolox_s.onnx
ADD --checksum=sha256:427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7 \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx /models/yolox_tiny.onnx
ADD --checksum=sha256:c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx /models/yolox_nano.onnx
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/detector/backends.py stages/detector/yolox_onnx.py stages/detector/main.py /app/
CMD ["python", "/app/main.py"]
