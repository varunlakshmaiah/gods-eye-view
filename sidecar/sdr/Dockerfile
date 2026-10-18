FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    rtl-sdr \
    librtlsdr-dev \
    usbutils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    httpx>=0.27.0 \
    redis>=5.0.0 \
    asyncpg>=0.29.0

COPY feeder.py ./

CMD ["python", "feeder.py"]
