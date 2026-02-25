# Order Management System

A full-stack order management system for reselling: track orders (with store and optional store account), items (purchase/sell price, buying group, status workflow), multiple payment methods per order, payments with line items, shipments (items from one or more orders can ship together), and rewards. Supports importing orders from store order pages via a browser extension.

## Stack

- **Backend:** Python, FastAPI, SQLAlchemy, PostgreSQL, Alembic migrations, JWT auth
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS
- **Database:** PostgreSQL (multi-user ready; `user_id` on orders, stores, etc.)

## Quick start (Docker)

Run everything with one command:

```bash
cd order-management
docker compose up --build
```

- **App:** http://localhost:5173  
- **API docs:** http://localhost:8000/docs  
- **PostgreSQL:** localhost:5432 (user `postgres`, password `postgres`, db `order_management`)

The backend runs migrations and ensures an admin user on startup (from `ADMIN_USERNAME` / `ADMIN_PASSWORD`, default `admin` / `admin`). Log in at the app, then create stores, buying groups, payment methods, and orders.

To run in the background: `docker compose up -d --build`.

---

### Running without Docker

**1. PostgreSQL** – Start Postgres and create a database, or use the db service only:

```bash
docker compose up -d db
```

**2. Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/order_management"
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=admin
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**3. Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and log in with the admin credentials.

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
- **Shipments:** `GET/POST /api/shipments`, `GET/PATCH/DELETE /api/shipments/{id}`

OpenAPI docs: http://localhost:8000/docs

## Browser extension

The **browser-extension** folder contains a Chrome/Firefox extension that can read order numbers from store order pages (e.g. Walmart). Use it to paste order numbers into the app for import/preview. See `browser-extension/README.md` for installation and usage.

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
├── browser-extension/
│   ├── manifest.json
│   ├── popup/
│   └── stores/
├── docker-compose.yml
└── README.md
```
