FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl gnupg unzip \
    xvfb fonts-liberation fonts-dejavu-core fonts-noto-core fonts-freefont-ttf \
    # Camoufox/Firefox GUI + audio libs (python:slim has none of these)
    libgtk-3-0 libx11-xcb1 libasound2 libdbus-glib-1-2 \
    libxt6 libxrender1 libxi6 libxcomposite1 libxdamage1 libxrandr2 \
    libxkbcommon0 libpangocairo-1.0-0 libpango-1.0-0 libcairo2 \
    libatk1.0-0 libatk-bridge2.0-0 libgdk-pixbuf-2.0-0 libglib2.0-0 \
    libnss3 libnspr4 libxcb1 libxshmfence1 libgbm1 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium for invoice HTML -> PDF. Camoufox for store login/import (+ Firefox OS deps).
RUN playwright install --with-deps chromium \
    && playwright install-deps firefox \
    && python -m camoufox fetch

COPY . .
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Virtual display for headed Camoufox (store automation). Invoice PDFs stay headless Chromium.
ENV DISPLAY=:99
# Docker/LXC often block user namespaces required by Firefox sandboxes.
ENV MOZ_DISABLE_CONTENT_SANDBOX=1
ENV MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1
ENV MOZ_ENABLE_WAYLAND=0

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
