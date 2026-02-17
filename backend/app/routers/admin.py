"""Admin API: user management and database reset (admin-only)."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.admin_bootstrap import ensure_admin_user
from app.auth import hash_password, require_admin
from app.database import get_db
from app.models import User
from app.schemas.user import UserRead, UserCreate, UserUpdate

# Tables in export order (parents before children) for backup
_BACKUP_TABLES = [
    "users",
    "buying_groups",
    "rewards",
    "payment_methods",
    "stores",
    "store_accounts",
    "orders",
    "shipments",
    "items",
    "order_payment_methods",
    "shipment_items",
    "payments",
    "payment_line_items",
]


def _json_serializer(obj):
    """Serialize datetime, date, Decimal for JSON."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

router = APIRouter(prefix="/admin", tags=["admin"])

# Tables in dependency order for truncate; CASCADE handles FKs
_TABLES = [
    "shipment_items",
    "order_payment_methods",
    "items",
    "orders",
    "store_accounts",
    "stores",
    "shipments",
    "payment_methods",
    "rewards",
    "buying_groups",
    "users",
]


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """List all users (admin only)."""
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserRead)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Create a new user (admin only)."""
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=data.username,
        email=data.email,
        name=data.name,
        role=data.role,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Update user (admin only). Password optional (only update if provided)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.username != user.username and db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user.username = data.username
    user.email = data.email
    user.name = data.name
    user.role = data.role
    if data.password:
        user.hashed_password = hash_password(data.password)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Delete a user (admin only). Cannot delete yourself."""
    # Prevent deleting self could be done by comparing to current user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete admin user")
    db.delete(user)
    db.commit()
    return None


@router.post("/reset-database")
def reset_database(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Truncate all tables and recreate admin from env (admin only)."""
    tables = ", ".join(_TABLES)
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.commit()
    ensure_admin_user(db)
    return {"message": "Database reset complete"}


@router.get("/backup")
def download_backup(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Export all tables as a JSON backup file (admin only)."""
    backup = {}
    for table in _BACKUP_TABLES:
        try:
            result = db.execute(text(f'SELECT * FROM "{table}"'))
            rows = result.fetchall()
            columns = result.keys()
            backup[table] = [dict(zip(columns, row)) for row in rows]
        except Exception:
            backup[table] = []
    content = json.dumps(backup, indent=2, default=_json_serializer)
    buffer = BytesIO(content.encode("utf-8"))
    buffer.seek(0)
    filename = f"order-management-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(
        buffer,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
