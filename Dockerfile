FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIVESIM_HOST=0.0.0.0 \
    LIVESIM_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-optional.txt
COPY livesim ./livesim

VOLUME ["/data"]
EXPOSE 8000 9999
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"
CMD ["python", "-m", "livesim"]
