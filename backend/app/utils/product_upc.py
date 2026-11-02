"""Store product barcodes are text: never infer them from retailer item numbers."""
import re


def product_upc(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if len(value) in (8, 12, 13, 14) and re.fullmatch(r"[0-9]+", value) else None
