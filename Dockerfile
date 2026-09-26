# syntax=docker/dockerfile:1
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*

# Copy project files needed for installation
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Install dependencies
RUN pip install --upgrade pip && pip install .

EXPOSE 8000

# APPINSIGHTS_CONNECTION_STRING should be provided at runtime for telemetry export
CMD ["uvicorn", "src.agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
