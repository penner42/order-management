# Development environment

This checkout is the Docker development environment. Production runs elsewhere.
Run application tools, dependency installation, builds, and tests inside Docker;
do not assume Node, Python packages, or browsers are installed on the host.

- Start services: `docker compose up -d --build --wait`
- Browser smoke tests: `docker compose --profile test run --build --rm frontend-tests`
- Frontend build/type check: `docker compose exec -T frontend npm run build`
- Backend unit tests: `docker compose exec -T backend python -m unittest discover -s tests -v`
- Apply migrations: `docker compose exec -T backend alembic upgrade head`

Source directories are bind-mounted. Vite provides frontend hot reload; both Python
services use Uvicorn reload. Rebuild after Python dependency or Dockerfile changes;
restart the frontend after frontend dependency changes to refresh its dependency
volume from the lockfile.

The browser runner reaches the app at `http://frontend:5173`, not localhost. It has
its own browser dependencies. Keep Playwright's npm version and Docker image tag
in sync. See README.md for reports and interactive debugging.

Preserve the existing database, invoice, and browser profile volumes. Tests should
create and clean up their own data; never reset or seed the shared development
database as part of a test run. Do not print `.env` contents or credentials.
