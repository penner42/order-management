"""UPC extraction from receipt product fields, including older cached responses."""
import re


def receipt_upc(row):
    raw = getattr(row, "raw", None) or {}
    # USABG retains the actual product under line; box/PO identifiers are unrelated.
    product = raw.get("line", raw)
    if not isinstance(product, dict):
        return None
    values = set()
    for key, value in product.items():
        if re.sub(r"[^a-z0-9]", "", str(key).casefold()) not in {
            "upc", "upca", "upccode", "upcnumber", "productupc", "itemupc",
        }:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        value = str(value).strip()
        if value and len(value) <= 64:
            values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def absorb_upc(meta, upc):
    """Only label an aggregate when every contributing product has the same UPC."""
    values = meta.setdefault("_receipt_upcs", set())
    values.add(upc)
    meta["receipt_upc"] = next(iter(values)) if len(values) == 1 else None
