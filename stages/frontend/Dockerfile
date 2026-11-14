FROM python:3.12-slim-trixie
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe "indah==0.13.0" httpx
COPY stages/frontend/app.py stages/frontend/common.py stages/frontend/detect.py stages/frontend/explore.py /app/
EXPOSE 8000
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
