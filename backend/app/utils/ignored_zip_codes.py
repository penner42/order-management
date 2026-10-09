"""Helpers for matching shipping postal codes against ignored zip codes."""
from __future__ import annotations

from typing import Iterable


def normalize_zip_code(zip_code: str) -> str:
    """Normalize postal codes for comparison (trim, case, strip spaces/hyphens)."""
    return zip_code.strip().upper().replace(" ", "").replace("-", "")


def is_ignored_postal_code(postal_code: object, ignored_zip_codes: Iterable[str]) -> bool:
    """True when the shipping postal code matches an ignored zip (exact or ZIP+4 prefix)."""
    if not isinstance(postal_code, str) or not postal_code.strip():
        return False
    ignored_list = [z for z in ignored_zip_codes if isinstance(z, str) and z.strip()]
    if not ignored_list:
        return False
    postal = normalize_zip_code(postal_code)
    if not postal:
        return False
    for z in ignored_list:
        ignored = normalize_zip_code(z)
        if not ignored:
            continue
        if postal == ignored or postal.startswith(ignored):
            return True
    return False


def shipping_postal_code_from_payload(payload: object) -> str | None:
    """Extract shippingAddress.postalCode from a validated or raw order payload."""
    shipping = getattr(payload, "shippingAddress", None)
    if shipping is None and isinstance(payload, dict):
        shipping = payload.get("shippingAddress")
    if not isinstance(shipping, dict):
        return None
    postal = shipping.get("postalCode")
    return postal if isinstance(postal, str) else None
