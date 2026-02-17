"""Order Management System - FastAPI application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, auth, users, buying_groups, rewards, payment_methods, payments, stores, store_accounts, orders, items, shipments
from app.admin_bootstrap import ensure_admin_user

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        ensure_admin_user(db)
    finally:
        db.close()

app.include_router(admin.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(buying_groups.router, prefix="/api")
app.include_router(rewards.router, prefix="/api")
app.include_router(payment_methods.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(store_accounts.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(items.router, prefix="/api")
app.include_router(shipments.router, prefix="/api")


@app.get("/")
def root():
    return {"message": "Order Management API", "docs": "/docs"}
