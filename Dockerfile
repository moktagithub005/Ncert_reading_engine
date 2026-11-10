FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin libgl1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
