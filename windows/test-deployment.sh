#!/bin/sh
# A disposable PostgreSQL database and isolated Compose project. Never uses .env.
set -eu
cd "$(dirname "$0")/.."
project_root=$PWD
# Match the owner of the bind-mounted checkout (GitHub runners may use UID 1001).
export LOCAL_UID="${LOCAL_UID:-$(id -u)}"
export LOCAL_GID="${LOCAL_GID:-$(id -g)}"
test_directory=$(mktemp -d)
test_project="order-manager-wincheck-$$"
compose() {
    docker compose --project-directory "$project_root" -p "$test_project" \
        --env-file "$test_directory/test.env" -f docker-compose.windows.yml -f "$test_directory/test.yml" "$@"
}
cleanup() {
    test_exit=$?
    if [ "$test_exit" -ne 0 ]; then
        compose logs --no-color backend
    fi
    compose --profile test down --volumes --remove-orphans
    rm -rf "$test_directory"
}
trap cleanup EXIT
cat > "$test_directory/test.env" <<'ENV'
SECRET_KEY=isolated-windows-deployment-validation-not-a-real-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
ENV
cat > "$test_directory/test.yml" <<'YAML'
services:
  frontend:
    ports: !override
      - "127.0.0.1::5173"
  backend:
    environment:
      # No scheduled third-party requests during automated tests.
      SCHEDULED_JOBS_ENABLED: "false"
      GROUP_SUBMISSIONS_ENABLED: "false"
      DATABASE_IMPORT_ENABLED: "true"
  frontend-tests:
    profiles: [test]
    build:
      context: ./frontend
      dockerfile: Dockerfile.test
    init: true
    user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"
    shm_size: 2gb
    environment:
      PLAYWRIGHT_BASE_URL: http://frontend:5173
      TEST_ADMIN_USERNAME: admin
      TEST_ADMIN_PASSWORD: admin
    volumes:
      - ./frontend:/app
      - /app/node_modules
    depends_on:
      frontend:
        condition: service_healthy
YAML
compose config --quiet
compose up -d --build --wait --wait-timeout 600
compose exec -T backend python -m unittest discover -s tests -v
compose exec -T backend python - change < windows/Tests/runtime-check.py
compose restart backend
compose up -d --wait --wait-timeout 600 backend
compose exec -T backend python - verify < windows/Tests/runtime-check.py
compose exec -T backend python - reset < windows/Tests/runtime-check.py
# Exercise the real backup/copy/restore commands against only this test database.
compose exec -T db pg_dump -U postgres -d order_management -Fc -f /tmp/validation.dump
compose exec -T db pg_restore --list /tmp/validation.dump > /dev/null
compose cp db:/tmp/validation.dump "$test_directory/backup with spaces.dump"
compose cp "$test_directory/backup with spaces.dump" db:/tmp/verify.dump
compose exec -T db pg_restore --list /tmp/verify.dump > /dev/null
compose exec -T db createdb -U postgres order_manager_restore_check
compose exec -T db pg_restore -U postgres -d order_manager_restore_check --no-owner --single-transaction --exit-on-error /tmp/verify.dump
compose exec -T db psql -U postgres -d order_manager_restore_check -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM users'
compose exec -T db dropdb -U postgres order_manager_restore_check
compose --profile test build frontend-tests
compose --profile test run --no-deps --rm frontend-tests
