"""Screenshot staging, semantic comparison, and transactional history attachment."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import time

from app.config import settings
from app.models import BrowserProfile, Order, OrderScreenshot

logger = logging.getLogger(__name__)
_last_cleanup = 0.0
CAPTURE_LAYOUT_VERSION = 3


def screenshot_dir() -> Path:
    root = Path(settings.invoice_dir) / "screenshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def semantic_snapshot(payload: dict) -> dict:
    """Compare business data, excluding generated IDs and visual/enrichment metadata.

    Resolve shipment references to tracking numbers so generated shipment IDs and
    reordering cannot create versions. Sorting preserves duplicate purchase lines.
    """
    tracking = {str(s.get("shipmentId")): s.get("trackingNumber")
                for s in payload.get("shipments") or []}

    def clean(value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key.lower().endswith("url") or key in {
                    "upc", "image", "capturedAt", "logicalItemId", "externalSku",
                    "externalOfferId", "groupId", "purchaseOrderId",
                    "rawStatusCode", "rawStatusType", "rawStatus", "rawStatusMessage",
                }:
                    continue
                if key == "shipmentId":
                    key, child = "trackingNumber", tracking.get(str(child))
                normalized = clean(child)
                if normalized is not None and normalized != {} and normalized != []:
                    result[key] = normalized
            return result
        if isinstance(value, list):
            return sorted((clean(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
        if isinstance(value, str):
            return " ".join(value.split()) or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(float(value), 4)
        return value

    return clean({key: payload.get(key) for key in (
        "externalOrder", "shippingAddress", "shipments", "items", "totals",
        "paymentMethods", "orderDiscount", "cancellations",
    )})


def fingerprint(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def latest_screenshot(db, order_id: int):
    return db.query(OrderScreenshot).filter_by(order_id=order_id).order_by(OrderScreenshot.id.desc()).first()


def profile_order(db, profile_id: int, number: str):
    profile = db.get(BrowserProfile, profile_id)
    if profile is None:
        return None
    return db.query(Order).filter_by(
        store_id=profile.store_id, store_account_id=profile.store_account_id,
        store_order_number=number,
    ).first()


def change_summary(before: dict | None, after: dict) -> str:
    if before is None:
        return "Initial capture"
    labels = {"externalOrder": "Order details", "shippingAddress": "Shipping address",
              "shipments": "Shipment status / tracking / delivery", "items": "Items / prices / status",
              "totals": "Totals", "paymentMethods": "Payment summary",
              "orderDiscount": "Discount", "cancellations": "Cancellations"}
    return "; ".join(label for key, label in labels.items() if before.get(key) != after.get(key)) or "Order details"


def cleanup_screenshots(db):
    """Collect abandoned stages and orphan files, never recent or referenced images."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 3600:
        return
    cutoff = now - 24 * 3600
    root = screenshot_dir()
    referenced = {row[0] for row in db.query(OrderScreenshot.filename).all()}
    for path in root.glob("*.png"):
        if path.name not in referenced and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
    stage = root / "staging"
    if stage.exists():
        for path in stage.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    _last_cleanup = now


def attach_screenshot(db, order: Order, payload):
    """Only accept opaque server-created stages belonging to this exact account/order.

    The order lock serializes concurrent writers; compare against the latest
    successful image rather than all past fingerprints to retain reversals.
    """
    token = payload.screenshotToken
    if not token or not re.fullmatch(r"[a-f0-9]{32}", token):
        return
    stage = screenshot_dir() / "staging"
    try:
        metadata = json.loads((stage / f"{token}.json").read_text())
        if (metadata["number"] != order.store_order_number
                or metadata["store_id"] != order.store_id
                or metadata["store_account_id"] != order.store_account_id
                or metadata["retailer"] != payload.store.lower()
                or metadata["snapshot"] != semantic_snapshot(payload.model_dump())):
            logger.warning("Screenshot stage does not match applied order %s", order.id)
            return
        db.query(Order).filter_by(id=order.id).with_for_update().one()
        prior = latest_screenshot(db, order.id)
        image = screenshot_dir() / f"{token}.png"
        if db.query(OrderScreenshot.id).filter_by(filename=image.name).first():
            return  # Applying the same review session again is idempotent.
        captured_at = datetime.fromisoformat(metadata["captured_at"])
        if prior and captured_at <= prior.captured_at.replace(tzinfo=timezone.utc):
            return  # A late import/review must not replace a newer observation.
        layout_version = int(metadata.get("layout_version", 1))
        same_state = prior is not None and prior.fingerprint == metadata["fingerprint"]
        if same_state:
            if layout_version < prior.layout_version:
                return
            if prior.layout_version == layout_version and (screenshot_dir() / prior.filename).is_file():
                return
        source = stage / f"{token}.png"
        # Copy before inserting the row. Rollback leaves a collectable orphan;
        # retaining the stage allows a failed transaction to be retried.
        shutil.copyfile(source, image)
        if same_state:
            # Framing changes and missing-image repairs update the latest
            # observation rather than inventing an order change in history.
            prior.filename = image.name
            prior.captured_at = captured_at
            prior.layout_version = layout_version
            return
        db.add(OrderScreenshot(
            order_id=order.id, captured_at=captured_at,
            filename=image.name, fingerprint=metadata["fingerprint"], snapshot=metadata["snapshot"],
            summary=change_summary(prior.snapshot if prior else None, metadata["snapshot"]),
            layout_version=layout_version,
        ))
    except (OSError, ValueError, KeyError):
        logger.warning("Could not attach screenshot for order %s", order.id, exc_info=True)
