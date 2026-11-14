# Training extras on top of the GPU detector image, so the inference image stays lean.
ARG BASE=geo-pipeline-detector-gpu
FROM ${BASE}
RUN pip install --no-cache-dir "rfdetr[train]"
# Triton kernels and YOLOX's COCO-eval extension are compiled on first use.
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# YOLOX's own trainer (Apache-2.0). Its setup.py cannot build here, so the pinned source tree is
# put on PYTHONPATH; the C++ COCO-eval extension is optional.
RUN pip install --no-cache-dir ninja loguru thop tabulate tensorboard scikit-image pycocotools onnx psutil
ADD --checksum=sha256:b85cfceda3538e3a00f6069192c89a1da7c1e39e77272a3d68d73e0841544fca \
    https://github.com/Megvii-BaseDetection/YOLOX/archive/419778480ab6ec0590e5d3831b3afb3b46ab2aa3.tar.gz /tmp/yolox.tgz
RUN mkdir /opt/yolox && tar xzf /tmp/yolox.tgz -C /opt/yolox --strip-components=1 && rm /tmp/yolox.tgz
ADD --checksum=sha256:f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30 \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth /models/yolox_s.pth
ENV PYTHONPATH=/opt/yolox
