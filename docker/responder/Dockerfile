FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    build-essential net-tools iproute2 \
    python3-dev \
    libffi-dev \
    openssl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir netifaces aioquic

RUN git clone --depth=1 https://github.com/lgandx/Responder.git /opt/responder

WORKDIR /opt/responder

# Ensure responder dirs exist
RUN mkdir -p logs

ENTRYPOINT ["python3", "Responder.py"]
