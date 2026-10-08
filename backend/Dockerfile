FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl gnupg unzip \
    xvfb fonts-liberation fonts-dejavu-core fonts-noto-core fonts-freefont-ttf \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bundled Chromium still used for invoice HTML -> PDF and as automation fallback
RUN playwright install --with-deps chromium

COPY . .
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Virtual display for headed Chrome (store automation). Invoice PDFs stay headless.
ENV DISPLAY=:99
ENV BROWSER_CHANNEL=chrome

# Entrypoint: Xvfb + migrations, then exec CMD (admin user is ensured on app startup)
RUN set -e && \
    printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'if [ -z "$DISPLAY" ]; then export DISPLAY=:99; fi' \
    'if command -v Xvfb >/dev/null 2>&1; then' \
    '  if [ ! -e "/tmp/.X${DISPLAY#:}-lock" ]; then' \
    '    echo "Starting Xvfb on $DISPLAY..."' \
    '    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &' \
    '    sleep 0.5' \
    '  fi' \
    'fi' \
    'echo "Running migrations..."' \
    'alembic upgrade head' \
    'echo "Starting API..."' \
    'exec "$@"' \
    > /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
