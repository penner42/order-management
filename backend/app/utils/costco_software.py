"""Identify software and Allstate plans included with Costco product bundles."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


# Match software titles, not hardware titles such as Microsoft Surface bundles.
_INCLUDED_ITEM_TITLE = re.compile(
    r"^(?:microsoft\s+(?:office\b|365\b)|mcafee\b|norton\b|"
    r"adobe\s+(?:acrobat\b|creative\b|photoshop\b)|"
    r"(?:antivirus|security)\s+software\b|allstate\b)",
    re.IGNORECASE,
)


def bundled_no_payment_names(store_name: str, items: list[dict]) -> set[str]:
    if store_name.strip().lower() != "costco":
        return set()
    names = {(item.get("name") or "").strip() for item in items}
    included = {name for name in names if _INCLUDED_ITEM_TITLE.search(name)}
    # Software and protection plans alone do not establish a product bundle.
    return included if names - included - {""} else set()


def costco_sales_tax(normalized: dict) -> Decimal | None:
    """Read captured order tax, preserving an explicit zero and missing values."""
    for source in (normalized.get("totals") or {}, normalized.get("externalOrder") or {}):
        for key in ("tax", "salesTax", "taxTotal", "totalTax"):
            try:
                value = Decimal(str(source.get(key)))
            except (InvalidOperation, ValueError):
                continue
            if value.is_finite() and value >= 0:
                return value
    return None


def default_costco_payouts(normalized: dict) -> list[float | None]:
    """Per-unit payouts including bundle credits, order discount, and sales tax."""
    return _costco_default_amounts(normalized)[0]


def default_costco_item_taxes(normalized: dict) -> list[Decimal | None]:
    """Per-unit tax shares for the item's purchase-cost display."""
    return _costco_default_amounts(normalized)[1]


def _costco_default_amounts(normalized: dict) -> tuple[list[float | None], list[Decimal | None]]:
    items = normalized.get("items") or []
    included = bundled_no_payment_names("costco", items)

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
    is_included = [(item.get("name") or "").strip() in included for item in items]
    included_cost = sum((unit or Decimal(0)) * qty for unit, qty, inc
                        in zip(units, quantities, is_included) if inc)
    subtotal = sum(max(Decimal(0), unit or Decimal(0)) * max(Decimal(0), qty)
                   for unit, qty, inc in zip(units, quantities, is_included) if not inc)
    discount = max(Decimal(0), (number(normalized.get("orderDiscount")) or Decimal(0)) - included_cost)
    discount = min(discount, subtotal)
    captured_tax = costco_sales_tax(normalized)
    tax = captured_tax or Decimal(0)
    payouts = [0.0 if inc else None if unit is None else float(
        (unit * (1 + (tax - discount) / subtotal) if subtotal > 0 and unit > 0 else unit)
        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ) for unit, inc in zip(units, is_included)]
    taxes = [None if captured_tax is None or unit is None else (
        unit * tax / subtotal if not inc and subtotal > 0 and unit > 0 else Decimal(0)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for unit, inc in zip(units, is_included)]
    return payouts, taxes
