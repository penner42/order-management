"""Shared BG receipt-credit identities and cent-exact allocation."""
from decimal import Decimal

ZERO = Decimal("0.00")


def tracking_key(value):
    return "".join(str(value or "").split()).casefold().replace("-", "")


def reward_share(total, before, quantity, denominator):
    """Reserve cents for unmatched units as well as matched ones."""
    if denominator <= 0 or before < 0 or before + quantity > denominator:
        raise ValueError("Reward quantity exceeds confirmed receipt quantity")
    unit = Decimal(total) / Decimal(denominator)
    return ((unit * (before + quantity)).quantize(Decimal("0.01"))
            - (unit * before).quantize(Decimal("0.01")))


def tracking_credits(receipts, rewards, alias_groups=()):
    """Rows: receipt_id, trackings, sort_date, void. Earliest receipt gets credit.

    Both history adapters use this rule; aliases form one connected identity.
    A missing reward is unknown, while an explicit zero is evidence.
    """
    components = []
    for aliases in alias_groups:
        aliases = {tracking_key(t) for t in aliases if tracking_key(t)}
        overlapping = [c for c in components if c["aliases"] & aliases]
        for other in overlapping:
            aliases.update(other["aliases"])
            components.remove(other)
        components.append({"aliases": aliases, "rows": []})
    for row in receipts:
        aliases = {tracking_key(t) for t in row["trackings"] if tracking_key(t)}
        if not aliases:
            continue
        overlapping = [c for c in components if c["aliases"] & aliases]
        component = {"aliases": set(aliases), "rows": [row]}
        for other in overlapping:
            component["aliases"].update(other["aliases"])
            component["rows"].extend(other["rows"])
            components.remove(other)
        components.append(component)
    credits = {}
    for component in components:
        if not component["rows"]:
            continue
        values = {Decimal(rewards[k]) for k in component["aliases"] if k in rewards}
        if len(values) > 1:
            raise ValueError("Tracking reward identities disagree")
        if not values:
            continue
        rows = sorted(component["rows"], key=lambda r: (r.get("sort_date") or "", r["receipt_id"]))
        eligible = [r for r in rows if not r.get("void")]
        credited = eligible[0]["receipt_id"] if eligible else None
        display = rows[0]["trackings"][0]
        credit = {"tracking_number": display, "aliases": sorted(component["aliases"]),
                  "amount": str(values.pop()), "receipt_id": credited}
        for row in rows:
            credits[row["receipt_id"]] = credit
    return credits
