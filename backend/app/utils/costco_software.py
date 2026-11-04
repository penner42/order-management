"""Identify separate software lines supplied with Costco product bundles."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


# Match software titles, not hardware titles such as Microsoft Surface bundles.
_SOFTWARE_TITLE = re.compile(
    r"^(?:microsoft\s+(?:office\b|365\b)|mcafee\b|norton\b|"
    r"adobe\s+(?:acrobat\b|creative\b|photoshop\b)|"
    r"(?:antivirus|security)\s+software\b)",
    re.IGNORECASE,
)


def bundled_software_names(store_name: str, items: list[dict]) -> set[str]:
    if store_name.strip().lower() != "costco":
        return set()
    names = {(item.get("name") or "").strip() for item in items}
    software = {name for name in names if _SOFTWARE_TITLE.search(name)}
    # A software-only order does not establish a product bundle.
    return software if names - software - {""} else set()


def default_costco_payouts(normalized: dict) -> list[float | None]:
    """Per-unit payouts after the bundle credit and proportional order discount."""
    items = normalized.get("items") or []
    software = bundled_software_names("costco", items)

    def number(value):
        try:
            result = Decimal(str(value))
            return result if result.is_finite() else None
        except (InvalidOperation, ValueError):
            return None

    quantities = [number((item.get("quantities") or {}).get("ordered")) or Decimal(1)
                  for item in items]
    units = []
    for item, qty in zip(items, quantities):
        pricing = item.get("pricing") or {}
        unit = number(pricing.get("unitPrice"))
        if unit is None:
            line = number(pricing.get("lineTotal"))
            if line is None:
                line = number(pricing.get("linePrice"))
            unit = line / qty if line is not None and qty > 0 else None
        units.append(unit)
    is_software = [(item.get("name") or "").strip() in software for item in items]
    software_cost = sum((unit or Decimal(0)) * qty for unit, qty, sw
                        in zip(units, quantities, is_software) if sw)
    subtotal = sum(max(Decimal(0), unit or Decimal(0)) * max(Decimal(0), qty)
                   for unit, qty, sw in zip(units, quantities, is_software) if not sw)
    discount = max(Decimal(0), (number(normalized.get("orderDiscount")) or Decimal(0)) - software_cost)
    discount = min(discount, subtotal)
    return [0.0 if sw else None if unit is None else float(
        (unit * (1 - discount / subtotal) if subtotal > 0 and unit > 0 else unit)
        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ) for unit, sw in zip(units, is_software)]
