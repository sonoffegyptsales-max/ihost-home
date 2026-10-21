# Tuya Bridge for SONOFF iHost - runs as an iHost Docker add-on (linux/arm/v7) or anywhere else.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=8321 \
    IHOST_HOST=http://127.0.0.1

WORKDIR /app
COPY requirements.txt .
# cryptography ships prebuilt wheels for armv7l / aarch64 / x86_64 (no compiler needed)
RUN pip install --only-binary=cryptography -r requirements.txt
COPY bridge.py .
COPY web ./web

VOLUME ["/data"]
EXPOSE 8321
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8321')+'/healthz',timeout=4)" || exit 1

CMD ["python", "/app/bridge.py"]
