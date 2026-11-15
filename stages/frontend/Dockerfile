FROM python:3.12-slim-trixie
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe "indah==0.14.0" httpx
COPY stages/frontend/app.py stages/frontend/common.py stages/frontend/detect.py stages/frontend/explore.py stages/frontend/models.py stages/frontend/train.py stages/frontend/finetune.py stages/frontend/modelupload.py /app/
EXPOSE 8000
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
