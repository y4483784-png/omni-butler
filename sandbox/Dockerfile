# Code-execution sandbox image (Phase 3, ADR-004)
# Runs Python data-analysis code with NO network and NO host filesystem access.
FROM python:3.11-slim

# Disable network at container runtime via `docker run --network=none`
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei \
    pkg-config \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir \
    "numpy>=1.26" \
    "pandas>=2.2" \
    "matplotlib>=3.8" \
    "openpyxl>=3.1" \
    "duckdb>=1.0"

WORKDIR /sandbox
COPY . /sandbox

# Run as non-root, read-only root fs, drop all caps in production launcher
CMD ["python", "/sandbox/run.py"]
