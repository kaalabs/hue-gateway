FROM python:3.12-slim

LABEL org.opencontainers.image.title="hue-gateway" \
    org.opencontainers.image.description="LAN-only Hue Gateway API server (v1+v2)" \
    com.rrk.project="hue-gateway"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN useradd -m -u 10001 app \
  && mkdir -p /data \
  && chown -R app:app /data

COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir .

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=2s --retries=5 CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/healthz').read()" || exit 1

CMD ["python", "-m", "hue_gateway"]
