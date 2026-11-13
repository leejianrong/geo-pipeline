FROM python:3.12-slim-trixie
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir "/src/geopipe[api]"
EXPOSE 8000
CMD ["uvicorn", "geopipe.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
