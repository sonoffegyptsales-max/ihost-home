# Tuya Bridge for SONOFF iHost - runs as an iHost Docker add-on (linux/arm/v7) or anywhere else.

# ---- stage 1: build wheels (cffi has no prebuilt armv7 wheel, so it needs a compiler here)
FROM python:3.12-slim AS wheels
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# ---- stage 2: small runtime image (no compiler)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=8321 \
    IHOST_HOST=http://127.0.0.1

WORKDIR /app
COPY --from=wheels /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels
COPY bridge.py .
COPY web ./web

VOLUME ["/data"]
EXPOSE 8321
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8321')+'/healthz',timeout=4)" || exit 1

CMD ["python", "/app/bridge.py"]
