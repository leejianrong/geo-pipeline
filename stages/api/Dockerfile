FROM python:3.12-slim-trixie
# Security fixes released since the base image was built (re-scan with `make scan`).
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U setuptools wheel
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir "/src/geopipe[api]"
EXPOSE 8000
CMD ["uvicorn", "geopipe.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
