"""UPC extraction from receipt product fields, including older cached responses."""
import re
from html import unescape
from urllib.parse import urlsplit


def _linked_upcs(value):
    """Read canonical item URLs without visiting the linked site."""
    upcs = set()
    for match in re.finditer(r'(?:https?:)?//[^\s<>"\'()]+', unescape(value), re.IGNORECASE):
        try:
            url = urlsplit(match.group())
        except ValueError:
            continue
        if url.hostname not in {"upcitemdb.com", "www.upcitemdb.com"} or url.username or url.password:
            continue
        product = re.fullmatch(r"/upc/([0-9]{1,64})/?", url.path)
        if product:
            upcs.add(product[1])
    return upcs


def receipt_upc(row):
    raw = (row if isinstance(row, dict) else getattr(row, "raw", None)) or {}
    # USABG retains the actual product under line; box/PO identifiers are unrelated.
    product = raw.get("line", raw)
    if not isinstance(product, dict):
        return None
    values = set()
    for key, value in product.items():
        field = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        explicit = field in {"upc", "fupc", "upca", "upccode", "upcnumber", "productupc", "itemupc"}
        linked = explicit or field in {
            "item", "fitem", "product", "itemurl", "itemlink", "producturl", "productlink",
            "upcurl", "upclink", "flink",
        }
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        value = str(value).strip()
        if linked:
            values.update(_linked_upcs(value))
        # An HTML anchor, URL, or spreadsheet hyperlink is never itself a UPC.
        if explicit and value and len(value) <= 64 and not any(
            marker in value for marker in ("://", "//", "<", ">", "=")
        ):
            values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def absorb_upc(meta, upc):
    """Only label an aggregate when every contributing product has the same UPC."""
    values = meta.setdefault("_receipt_upcs", set())
    values.add(upc)
    meta["receipt_upc"] = next(iter(values)) if len(values) == 1 else None
