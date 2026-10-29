FROM docker.io/library/python:3.14-slim
WORKDIR /srv
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 homedash && mkdir /data && chown homedash /data
COPY server.py ./
COPY connectors ./connectors
COPY static ./static
USER homedash
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOMEDASH_DATA=/data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
