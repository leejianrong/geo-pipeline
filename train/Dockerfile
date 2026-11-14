# Training extras on top of the GPU detector image, so the inference image stays lean.
ARG BASE=geo-pipeline-detector-gpu
FROM ${BASE}
RUN pip install --no-cache-dir "rfdetr[train]"
# torch's Triton kernels are compiled on first use and need a C compiler.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
