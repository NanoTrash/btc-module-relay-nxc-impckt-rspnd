FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir impacket

# Create directories for loot and logs
RUN mkdir -p /loot /logs

WORKDIR /workspace

ENTRYPOINT ["python3", "-m"]
