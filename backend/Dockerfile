FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl gnupg unzip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium for invoice HTML -> PDF rendering (print-preview fidelity)
RUN playwright install --with-deps chromium

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
