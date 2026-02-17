# Order Management System

A full-stack order management system for reselling: track orders, items (with purchase/sell price and buying groups), multiple payment methods per order, item statuses (purchased → shipped → delivered → scanned → payment requested → paid), and shipments (items from one or more orders can ship together).

## Stack

- **Backend:** Python, FastAPI, SQLAlchemy, PostgreSQL, Alembic migrations
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS
- **Database:** PostgreSQL (single user for now; schema is multi-user ready)

## Quick start (Docker)

Run everything with one command:

```bash
cd order-management
docker compose up --build
```

- **App:** http://localhost:5173  
- **API docs:** http://localhost:8000/docs  
- **PostgreSQL:** localhost:5432 (user `postgres`, password `postgres`, db `order_management`)

The backend runs migrations and seeds a default user on startup. Create buying groups and payment methods in the app, then add orders.

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
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**3. Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Seed the default user once via `POST /api/users/seed` (or the backend does it automatically when run via Docker).

## Data model (multi-user ready)

- **Users** – One user for now; `user_id` is nullable on orders, groups, payment methods, and shipments so you can add auth later.
- **Orders** – Have notes; multiple **items** and multiple **payment methods** (with optional amount per method).
- **Items** – Purchase date, price paid, price sold, **buying group**, **status**: `purchased` → `shipped` → `delivered` → `scanned` → `payment_requested` → `paid`.
- **Payment methods** – Type: `credit_card`, `paypal` (with optional PayPal card reference), or `other`; label (e.g. "Visa ****1234", "PayPal - Amex").
- **Shipments** – Optional tracking and shipped date; **shipment_items** link items to shipments so multiple orders can share one shipment.

## API

- `GET/POST /api/orders`, `GET/PATCH/DELETE /api/orders/{id}`
- `GET/POST /api/items`, `GET/PATCH/DELETE /api/items/{id}` (optional `?order_id=`)
- `GET/POST /api/buying-groups`, `GET/PATCH/DELETE /api/buying-groups/{id}`
- `GET/POST /api/payment-methods`, `GET/PATCH/DELETE /api/payment-methods/{id}`
- `GET/POST /api/shipments`, `GET/PATCH/DELETE /api/shipments/{id}`
- `GET /api/users/me`, `POST /api/users/seed`

OpenAPI docs: http://localhost:8000/docs

## Project layout

```
order-management/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models/
│   │   ├── schemas/
│   │   └── routers/
│   ├── alembic/
│   │   └── versions/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── pages/
│   │   └── App.tsx
│   └── package.json
├── docker-compose.yml
└── README.md
```
