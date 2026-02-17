#!/bin/sh
set -e
echo "Running migrations..."
alembic upgrade head
echo "Starting API..."
exec "$@"
