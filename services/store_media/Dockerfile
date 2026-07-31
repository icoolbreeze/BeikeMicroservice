FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY static ./static
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage/uploads && chown -R appuser:appuser /app/storage

USER appuser
EXPOSE 8010
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
