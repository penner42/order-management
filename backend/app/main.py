"""Order Management System - FastAPI application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    admin,
    auth,
    users,
    buying_groups,
    rewards,
    payment_methods,
    payments,
    stores,
    store_accounts,
    orders,
    items,
    shipments,
    portals,
    store_imports,
    browser_extension,
    browser_profiles,
)
from app.admin_bootstrap import ensure_admin_user

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^(chrome|moz)-extension://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    from app.database import SessionLocal
    from app.utils.browser_extension import ensure_signed_async
    from app.browser_automation.scheduler import start_scheduler
    from app.routers.browser_profiles import clear_stale_browser_profile_statuses

    db = SessionLocal()
    try:
        ensure_admin_user(db)
        cleared = clear_stale_browser_profile_statuses(db)
        if cleared:
            import logging

            logging.getLogger(__name__).info(
                "Cleared %s browser profile(s) stuck in importing/login_in_progress",
                cleared,
            )
    finally:
        db.close()

    ensure_signed_async()
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown():
    from app.browser_automation.scheduler import stop_scheduler

    await stop_scheduler()

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
app.include_router(portals.router, prefix="/api")
app.include_router(store_imports.router, prefix="/api")
app.include_router(browser_extension.router, prefix="/api")
app.include_router(browser_profiles.router, prefix="/api")


@app.get("/")
def root():
    return {"message": "Order Management API", "docs": "/docs"}
