FROM python:3.12-slim-trixie
COPY libs/geopipe /src/geopipe
RUN pip install --no-cache-dir /src/geopipe
COPY stages/detector/main.py /app/main.py
CMD ["python", "/app/main.py"]
