FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir git+https://github.com/Pennyw0rth/NetExec.git

WORKDIR /workspace

ENTRYPOINT ["nxc"]
