# WARNING
## This whole thing is AI slop. Do not make it accessible from the internet. Do not trust any of the security features. Only run it on your local LAN and if possible, restrict access to it even there.

# Order Management System

A full-stack order management system for reselling: track orders (with store and optional store account), items (purchase/sell price, buying group, status workflow), multiple payment methods per order, payments with line items, shipments (items from one or more orders can ship together), and rewards. Supports importing orders from store order pages via a browser extension.

## Stack
- **Backend:** Python, FastAPI, SQLAlchemy, PostgreSQL, Alembic migrations, JWT auth
- **Package tracking service:** FastAPI (UPS tracking; proxied by the frontend)
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS
- **Database:** PostgreSQL (multi-user ready; `user_id` on orders, stores, etc.)

## Development (Docker only)

The current Compose stack is the development environment. The host only needs
Docker Engine and Docker Compose; Node, Python, browsers, and dependencies run in
containers. Production is deployed separately.

On a fresh checkout, copy `.env.sample` to `.env` and set the admin credentials.
Existing checkouts can keep their `.env` and data volumes.

```bash
docker compose up -d --build --wait
```

- **App:** http://localhost:5173
- **Backend API docs:** http://localhost:8000/docs (bound to host loopback)
- **Package tracking docs:** http://localhost:8080/docs
- **PostgreSQL:** localhost:5432 (`postgres` / `postgres`, database `order_management`)

The backend applies migrations and ensures the admin user from `.env` at startup.
Sign in with those credentials. The admin can create regular users for the order
management screens.

Frontend, backend, and tracking source directories are mounted into their
containers. Vite updates the browser when frontend code changes; Uvicorn reloads
when Python code changes. Compose Watch is no longer required. Migrations added
while the backend is running can be applied with
`docker compose exec backend alembic upgrade head`.

Frontend dependencies live in a Docker volume, separate from host `node_modules`.
`npm ci` runs on frontend startup. After changing its lockfile, restart the frontend:
`docker compose restart frontend`. After changing Python requirements or Dockerfiles,
run `docker compose up -d --build --wait` again.

### Tests and build checks

Run the real browser tests entirely in Docker:

```bash
docker compose --profile test run --build --rm frontend-tests
```

This builds a Playwright runner with matching browser and npm package versions,
waits for healthy app services, and accesses the app at `http://frontend:5173` on
the Compose network. Inside a runner, `localhost` refers to that runner itself.
The runner uses a separate dependency volume and leaves the development server's
dependencies alone. See the [Playwright Docker documentation](https://playwright.dev/docs/docker)
for the browser image requirements.

The smoke suite checks authentication, regular-user orders/stores, and both API
proxies against the real backend, tracking service, and PostgreSQL. It reads admin
credentials from `.env`, creates a uniquely named temporary regular user, and deletes
that user in cleanup. It does not reset or seed the database. If a runner is forcibly
terminated, an `e2e-` user may remain and can be deleted in Admin.

Reports are saved in `frontend/playwright-report/`; failure screenshots and traces
are in `frontend/test-results/`. The runner defaults to UID/GID 1000 so reports
remain editable on the host. On Linux with a different user ID, set
`LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)` before the Compose command.
To view the report from a container:

```bash
docker compose --profile test run --rm --no-deps -p 127.0.0.1:9323:9323 frontend-tests npx playwright show-report --host 0.0.0.0 --port 9323
```

Open http://localhost:9323. For interactive browser test debugging:

```bash
docker compose --profile test run --rm -p 127.0.0.1:9323:9323 frontend-tests npm run test:e2e:ui
```

Other development commands:

```bash
# TypeScript check and frontend bundle
docker compose exec frontend npm run build

# Backend unit tests (Python's built-in unittest runner)
docker compose exec backend python -m unittest discover -s tests -v

# Shells and logs
docker compose exec frontend sh
docker compose exec backend bash
docker compose logs -f backend frontend package-tracking

# Stop the environment, preserving database, invoices, and browser profiles
docker compose down
```

Use `docker compose down` without `-v` to preserve data. Source changes are visible
immediately through bind mounts; changes to container configuration require
`docker compose up -d --wait`.

The frontend proxies `/api` to `backend:8000` (including WebSockets) and `/tracking`
to `package-tracking:8080`. UPS status lookups require `UPS_CLIENT` / `UPS_SECRET`;
tracking link generation works without them.

## Data model (multi-user ready)

- **Users** – Admin and app users; auth via JWT. `user_id` is set on orders, stores, buying groups, payment methods, etc.
- **Stores & store accounts** – Orders belong to a store and optionally a store account (e.g. per Amazon account).
- **Orders** – Notes, purchase date, store order number (e.g. from retailer), **status**: `active` (default) or `imported` (excluded from main list). Multiple **items** and multiple **payment methods** (with optional amount per method).
- **Items** – Purchase/sell price, **buying group**, **status**: `purchased` → `shipped` → `submitted` → `scanned`; plus `canceled`, `needs_return`, `return_started`, `return_sent`, `return_received`, `return_refunded`. Optional `submission_id`, `receipt_id`, and status timestamps. Payment state (`payment_requested` → `payment_sent` → `payment_received`) is on **Payments** via `payment_requested_at`, `payment_sent_at`, `payment_received_at`.
- **Payment methods** – Type: `credit_card`, `paypal` (optional PayPal card reference), or `other`; label (e.g. "Visa ****1234").
- **Payments** – Track payments with **line items** linking to items (e.g. mark items as payment requested / received).
- **Rewards** – User-defined rewards (e.g. store reward programs).
- **Shipments** – Optional tracking and shipped date; **shipment_items** link items to shipments so multiple orders can share one shipment.

## API

All API routes are under `/api`. Auth: send `Authorization: Bearer <token>` (from `POST /api/auth/login`).

- **Auth:** `POST /api/auth/login`, `GET /api/auth/me`
- **Users:** `GET /api/users/me`, `GET /api/users/me/orders`, `PATCH /api/users/me/password`
- **Admin:** `GET/POST/PATCH/DELETE /api/admin/users`, `POST /api/admin/reset-database`, `GET /api/admin/backup`
- **Orders:** `GET/POST /api/orders`, `GET/PATCH/DELETE /api/orders/{id}` (list supports `order_status=imported`, `status`, `buying_group_id`, `date_from`, `date_to`)
- **Items:** `GET/POST /api/items`, `GET/PATCH/DELETE /api/items/{id}`, `POST /api/items/bulk-update`, `POST /api/items/bulk-delete`, `POST /api/items/{id}/split` (optional `?order_id=`)
- **Buying groups:** `GET/POST /api/buying-groups`, `GET/PATCH/DELETE /api/buying-groups/{id}`
- **Rewards:** `GET/POST /api/rewards`, `GET/PATCH/DELETE /api/rewards/{id}`
- **Payment methods:** `GET/POST /api/payment-methods`, `GET/PATCH/DELETE /api/payment-methods/{id}`, `GET /api/payment-methods/{id}/store-earnings`
- **Payments:** `GET/POST /api/payments`, `GET/PATCH/DELETE /api/payments/{id}`, `POST /api/payments/{id}/line-items`, `DELETE /api/payments/{id}/line-items/{line_item_id}`
- **Stores:** `GET/POST /api/stores`, `GET/PATCH/DELETE /api/stores/{id}`, `GET/POST /api/stores/{id}/accounts`, `GET/PATCH/DELETE /api/stores/{id}/accounts/{account_id}`
- **Store accounts:** `GET /api/store-accounts`
- **Browser profiles:** `GET/POST /api/browser-profiles`, `DELETE /api/browser-profiles/{id}`, `POST .../login/start|done|cancel`, `POST .../import`, `GET /api/browser-profiles/jobs/{job_id}`, WebSocket `.../live?token=`
- **Shipments:** `GET/POST /api/shipments`, `GET/PATCH/DELETE /api/shipments/{id}`

OpenAPI docs: http://localhost:8000/docs (available on host loopback in the development stack)

## Browser extension

The **browser-extension** folder contains a Chrome/Firefox extension that can read order numbers from store order pages (e.g. Walmart). Use it to paste order numbers into the app for import/preview. See `browser-extension/README.md` for installation and usage.

## Browser automation (Walmart / Amazon)

The app can run **Camoufox (anti-detect Firefox) on the API host** with one persistent profile per store account. Use **Browser automation** in the nav:

1. Create a Store Account (for Amazon, name it with the login email for import matching).
2. Add a browser profile and choose **Walmart** or **Amazon**.
3. Click **Log in** and complete sign-in (including MFA) in the embedded live view, then **Done**.
4. Click **Import now** to walk order history; when finished, open **Import Review** to apply orders.

Notes:

- Cookies live under `BROWSER_PROFILES_DIR` (default `data/browser_profiles`). Treat that directory as secret; it is gitignored.
- Concurrent browsers are capped by `BROWSER_MAX_CONCURRENT` (default 2).
- Set `APP_PUBLIC_BASE_URL` to the frontend origin used in review links (default `http://localhost:5173`).
- Store sites may challenge datacenter IPs; this works best when the backend runs on a trusted/home network.
- Automation browsers default to **headed Camoufox** (`BROWSER_HEADLESS=false`) under Xvfb in Docker. Login does **not** auto-navigate to the retailer — open the store from the live-view address bar. Invoice PDF rendering still uses headless Chromium separately.
- Profile data lives under `camoufox-profile-<id>/`. If a profile is stuck on a block page, **Delete** it and create a new one.
- Supported retailers: **Walmart**, **Amazon**, and **Costco**. Walmart uses Camoufox (Firefox); Amazon and Costco use Chromium (Costco’s Azure B2C login fails on Firefox via `SelfAsserted` XHR interrupt). The browser extension remains available for ad-hoc capture.
- Live view uses a WebSocket at `/api/browser-profiles/{id}/live` (same host/port as the site — no extra port). Behind a reverse proxy you must forward WebSocket upgrades for `/api`. If the upgrade is missing, backend logs show a plain `GET .../live` with **404/426** instead of a WebSocket accept.

Nginx example (TLS terminator → Vite/frontend on 5173, or directly to backend on 8000 for `/api`):

```nginx
location /api/ {
  proxy_pass http://127.0.0.1:5173;  # or :8000 if API is exposed directly
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;
}
```

## Project layout

```
order-management/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── auth.py
│   │   ├── admin_bootstrap.py
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── admin.py
│   │   │   ├── users.py
│   │   │   ├── orders.py
│   │   │   ├── items.py
│   │   │   ├── buying_groups.py
│   │   │   ├── rewards.py
│   │   │   ├── payment_methods.py
│   │   │   ├── payments.py
│   │   │   ├── stores.py
│   │   │   ├── store_accounts.py
│   │   │   └── shipments.py
│   │   └── utils/
│   ├── alembic/
│   │   └── versions/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── contexts/
│   │   ├── pages/
│   │   │   ├── Orders.tsx
│   │   │   ├── OrderDetail.tsx
│   │   │   ├── ImportPreview.tsx
│   │   │   ├── ImportedOrders.tsx
│   │   │   ├── BuyingGroups.tsx
│   │   │   ├── Rewards.tsx
│   │   │   ├── PaymentMethods.tsx
│   │   │   ├── Payments.tsx
│   │   │   ├── Shipments.tsx
│   │   │   ├── Stores.tsx
│   │   │   ├── Login.tsx
│   │   │   ├── Profile.tsx
│   │   │   └── Admin.tsx
│   │   └── App.tsx
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
├── package-tracking/
│   ├── app/main.py
│   ├── requirements.txt
│   └── Dockerfile
├── browser-extension/
│   ├── manifest.json
│   ├── popup/
│   └── stores/
├── docker-compose.yml
└── README.md
```
