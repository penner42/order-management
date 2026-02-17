FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Entrypoint: migrations, then exec CMD (admin user is ensured on app startup)
RUN set -e && \
    printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'echo "Running migrations..."' \
    'alembic upgrade head' \
    'echo "Starting API..."' \
    'exec "$@"' \
    > /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
