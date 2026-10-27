"""Complete, versioned database snapshots for production-to-development copies."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import MetaData, select, text

FORMAT = "order-management-database"
VERSION = 1


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def schema(connection):
    metadata = MetaData()
    metadata.reflect(connection)
    return [t for t in metadata.sorted_tables if t.name != "alembic_version"]


def snapshot(connection):
    tables = schema(connection)
    return {
        "format": FORMAT, "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "revisions": sorted(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()),
        "columns": {t.name: [c.name for c in t.columns] for t in tables},
        "tables": {t.name: [dict(r) for r in connection.execute(select(t)).mappings()] for t in tables},
    }


def encode(payload):
    return json.dumps(payload, default=json_default).encode("utf-8")


def validate(connection, payload, tables):
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("Select a complete database export from Admin > Database. Legacy JSON backups are incomplete and cannot be imported.")
    revisions = sorted(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if payload.get("revisions") != revisions:
        raise ValueError("Database versions differ. Update both environments to the same migration version and export again.")
    data, columns = payload.get("tables"), payload.get("columns")
    if not isinstance(data, dict) or set(data) != {t.name for t in tables} or not isinstance(columns, dict):
        raise ValueError("Export does not contain the complete database schema.")
    for table in tables:
        names = {c.name for c in table.columns}
        if set(columns.get(table.name, [])) != names or not isinstance(data[table.name], list):
            raise ValueError(f"Invalid schema for {table.name}.")
        for row in data[table.name]:
            if not isinstance(row, dict) or set(row) != names:
                raise ValueError(f"Invalid row in {table.name}.")
            for col in table.columns:
                value = row[col.name]
                if value is None:
                    continue
                if col.type.python_type is datetime:
                    row[col.name] = datetime.fromisoformat(value)
                elif col.type.python_type is date:
                    row[col.name] = date.fromisoformat(value)
                elif col.type.python_type is Decimal:
                    row[col.name] = Decimal(value)
    if not any(r.get("role") == "admin" for r in data.get("users", [])):
        raise ValueError("Export must include an administrator account.")
    return data


def restore(connection, payload, backup_dir):
    """Caller owns the transaction; any failure rolls back every table change."""
    tables = schema(connection)
    data = validate(connection, payload, tables)
    quote = connection.dialect.identifier_preparer.quote
    names = ", ".join(quote(t.name) for t in tables)
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    connection.execute(text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE"))
    # Preserve a full copy before replacing any rows, in the existing persistent volume.
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"before-import-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex}.json"
    with backup.open("xb") as target:
        backup.chmod(0o600)
        target.write(encode(snapshot(connection)))
    connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY"))
    for table in tables:
        rows = data[table.name]
        if table.name == "buying_groups":
            for row in rows:
                for field in ("tracking_submit_enabled", "received_check_enabled", "order_number_submit_enabled"):
                    row[field] = False
        if table.name == "browser_profiles":
            for row in rows:
                row["full_check_enabled"] = row["unshipped_check_enabled"] = False
                row["status"] = "logged_out"
        if table.name == "monarch_connections":
            for row in rows:
                row["daily_enabled"] = False
        # Self references may point to rows later in the export (e.g. card parents).
        self_refs = [c for c in table.columns if any(f.column.table is table for f in c.foreign_keys)]
        pending = []
        for row in rows:
            for col in self_refs:
                if row[col.name] is not None:
                    pending.append((row, col, row[col.name]))
                    row[col.name] = None
        for start in range(0, len(rows), 500):
            connection.execute(table.insert(), rows[start:start + 500])
        for row, col, value in pending:
            query = table.update()
            for pk in table.primary_key:
                query = query.where(pk == row[pk.name])
            connection.execute(query.values({col.name: value}))
        for col in table.primary_key:
            if col.type.python_type is not int:
                continue
            sequence = connection.execute(text("SELECT pg_get_serial_sequence(:table, :column)"),
                                          {"table": quote(table.name), "column": col.name}).scalar()
            if sequence:
                next_id = max([row[col.name] for row in rows] or [0]) + 1
                connection.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH {next_id}"))
    return {"message": "Database imported. Scheduled jobs are disabled. Sign in with an imported administrator account.",
            "backup_filename": backup.name, "rows_imported": sum(len(rows) for rows in data.values())}
