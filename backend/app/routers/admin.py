"""Admin API: user management and database reset (admin-only)."""
import json
import os
import subprocess
from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.admin_bootstrap import ensure_admin_user
from app.auth import hash_password, require_admin
from app.seed_dev import seed_dev_data
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas.user import UserRead, UserCreate, UserUpdate
from app.utils.database_transfer import snapshot, encode, restore


def _backups_dir() -> str:
    """Return the backups directory path, creating it if it does not exist."""
    # backend/app/routers/admin.py -> 4 dirnames -> repo root
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    backups_dir = os.path.join(repo_root, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    return backups_dir

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
    "ignored_zip_codes",
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


@router.post("/seed-dev")
def seed_dev(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Load sample development data (admin only). Idempotent: no-op if already loaded."""
    result = seed_dev_data(db)
    return {"message": result["message"], "skipped": result["skipped"]}


@router.get("/backup")
def download_backup(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Export all tables as a JSON backup file (admin only)."""
    with db.get_bind().connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            content = encode(snapshot(connection))
    buffer = BytesIO(content)
    filename = f"order-management-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(
        buffer,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/database-transfer")
def database_transfer_options(_: User = Depends(require_admin)):
    return {"import_enabled": settings.database_import_enabled}


@router.post("/import")
def import_database(
    file: UploadFile,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Replace a development database with a complete export, atomically."""
    if not settings.database_import_enabled:
        raise HTTPException(status_code=403, detail="Database import is disabled in this environment.")
    content = file.file.read(256 * 1024 * 1024 + 1)
    if len(content) > 256 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Database export exceeds the 256 MiB import limit.")
    try:
        payload = json.loads(content)
        bind = db.get_bind()
        # Release the auth query's read lock before locking every table for replacement.
        db.rollback()
        with bind.begin() as connection:
            result = restore(connection, payload, os.path.join(settings.invoice_dir, "database-backups"))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except SQLAlchemyError:
        # SQL errors may contain imported account credentials in query parameters.
        raise HTTPException(status_code=400, detail="Import failed database validation. Existing data was preserved.") from None
    except OSError:
        raise HTTPException(status_code=503, detail="Could not save the pre-import backup. Existing data was preserved.") from None
    return result


@router.post("/create-backup")
def create_pg_dump_backup(
    _: User = Depends(require_admin),
):
    """Create a pg_dump backup in the backups folder (admin only)."""
    url = settings.database_url
    if not url or not url.strip().startswith("postgresql"):
        raise HTTPException(status_code=400, detail="Database is not PostgreSQL; pg_dump backup not available")
    backups_dir = _backups_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"order-management-{stamp}.dump"
    path = os.path.join(backups_dir, filename)
    try:
        subprocess.run(
            ["pg_dump", "-d", url, "-Fc", "-f", path],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="pg_dump not found. Install PostgreSQL client tools to create backups.",
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=503,
            detail=f"pg_dump failed: {e.stderr.decode('utf-8', errors='replace') if e.stderr else str(e)}",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=503, detail="pg_dump timed out")
    return {"message": "Backup created", "filename": filename}
