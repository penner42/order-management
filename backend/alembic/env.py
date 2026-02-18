"""Alembic environment configuration."""
import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.config import fileConfig

from alembic import context

LOG = logging.getLogger("alembic")
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import op

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import Base
from app.models import (
    User,
    BuyingGroup,
    Reward,
    PaymentMethod,
    Store,
    StoreAccount,
    Order,
    OrderPaymentMethod,
    Item,
    Shipment,
    ShipmentItem,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run_pre_migration_backup() -> None:
    """Export a full DB backup to backups/ before running migrations (online mode only)."""
    url = config.get_main_option("sqlalchemy.url") or ""
    if not url or not url.strip().startswith("postgresql"):
        return
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backups_dir = os.path.join(repo_root, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backups_dir, f"pre-migration-{stamp}.dump")
    try:
        subprocess.run(
            ["pg_dump", "-d", url, "-Fc", "-f", path],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        # Do not block migrations (e.g. pg_dump not installed or DB unreachable)
        LOG.warning("Pre-migration backup failed (%s); continuing with migration.", e)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    _run_pre_migration_backup()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
