FROM python:3.12-slim-trixie
# CPU-only torch keeps the image far smaller than the default CUDA wheels.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir rfdetr
# Bake the pretrained weights in so the container runs offline.
RUN python -c "from rfdetr import RFDETRSmall; RFDETRSmall()"
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/detector/backends.py stages/detector/main.py /app/
CMD ["python", "/app/main.py"]
