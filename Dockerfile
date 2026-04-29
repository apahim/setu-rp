FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/
COPY config.yaml .
COPY pyproject.toml .

RUN pip install --no-cache-dir -e .
RUN chmod +x scripts/*.sh

ENV PYTHONPATH=/app/src
