"""Audit and repair Amazon tracking lost when packages shared a shipment ID."""
from __future__ import annotations

import argparse
import asyncio
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from app.browser_automation.amazon_import import _inject_amazon_scripts
from app.browser_automation.paths import profile_user_data_dir
from app.browser_automation.session_manager import looks_like_amazon_signin, session_manager
from app.config import settings
from app.database import SessionLocal
from app.models import BrowserProfile, Item, Order, Shipment, ShipmentItem
from app.models.item import ItemStatus


@dataclass(frozen=True)
class TrackingMove:
    item_id: int
    quantity: int
    before: str
    after: str
    status: str | None


def inspect_order(db, order_id, number, scope, captured, *, apply=False):
    """Return an order's repairable moves and apply them when explicitly requested."""
    if captured["orderId"] != number:
        raise ValueError("Amazon returned a different order")
    groups = defaultdict(list)
    for shipment in captured["shipments"]:
        base, separator, index = shipment["shipmentId"].rpartition(":package:")
        if base and separator and index.isdecimal():
            groups[base].append(shipment)
    collisions = [group for group in groups.values() if len(group) > 1]
    if not collisions:
        return "no package collision", []

    order_query = db.query(Order).filter_by(id=order_id, store_order_number=number, **scope)
    order = (order_query.with_for_update() if apply else order_query).one()
    item_query = db.query(Item).filter_by(order_id=order.id).order_by(Item.id)
    items = (item_query.with_for_update() if apply else item_query).all()
    link_query = db.query(ShipmentItem).filter(ShipmentItem.item_id.in_([i.id for i in items]))
    link_query = link_query.order_by(ShipmentItem.id)
    links = {link.item_id: link for link in (link_query.with_for_update() if apply else link_query).all()}
    shipment_query = db.query(Shipment).filter(Shipment.id.in_({link.shipment_id for link in links.values()}))
    shipment_query = shipment_query.order_by(Shipment.id)
    shipments = {s.id: s for s in (shipment_query.with_for_update() if apply else shipment_query).all()}
    matches = [
        [item for item in items if item.description == line["name"] and item.quantity == line["quantity"]
         and (not item.external_sku or item.external_sku == line.get("asin"))]
        for line in captured["items"]
    ]
    match_counts = Counter(item.id for matches_for_line in matches for item in matches_for_line)
    moves = []

    for group in collisions:
        packages = {s["shipmentId"]: s for s in group}
        tracking = [s.get("trackingNumber") for s in group]
        if len(packages) != len(group) or not all(tracking) or len(set(tracking)) != len(group):
            return "manual review: incomplete or ambiguous package tracking", []
        # The old importer mapped a shared shipment ID to its last package.
        retained_tracking = tracking[-1]
        seen = set()
        for line, candidates in zip(captured["items"], matches):
            package = packages.get(line.get("shipmentId"))
            if package is None:
                continue
            seen.add(package["shipmentId"])
            if len(candidates) != 1 or match_counts[candidates[0].id] != 1 or candidates[0].id not in links:
                return "manual review: item lines do not match uniquely", []
            item = candidates[0]
            current = shipments[links[item.id].shipment_id].tracking_number
            target = package["trackingNumber"]
            if current == target:
                continue
            if current != retained_tracking:
                return "manual review: current tracking differs from the expected old assignment", []
            if (item.status != ItemStatus.SHIPPED or item.payment_line_items or item.receipt_id
                    or item.receipt_tracking_number or item.submission_id or item.submission_api_id
                    or item.submitted_at or item.scanned_at):
                return "manual review: an affected line has been processed", []
            status = (package.get("status") or {}).get("message")
            moves.append(TrackingMove(item.id, item.quantity, current, target, status))
        if seen != packages.keys():
            return "manual review: a package has no matching item lines", []

    if not moves:
        return "assignments already correct", []

    targets = {move.after for move in moves}
    destinations = db.query(Shipment).filter(Shipment.tracking_number.in_(targets))
    destinations = destinations.order_by(Shipment.id)
    destinations = (destinations.with_for_update() if apply else destinations).all()
    by_tracking = {shipment.tracking_number: shipment for shipment in destinations}
    if len(by_tracking) != len(destinations) or any(s.user_id != order.user_id for s in destinations):
        return "manual review: a destination tracking number has conflicting shipment records", []
    if not apply:
        return "repairable", moves

    for move in moves:
        shipment = by_tracking.get(move.after)
        if shipment is None:
            shipment = Shipment(user_id=order.user_id, tracking_number=move.after,
                                status=move.status, notes="Recovered from Amazon order details.")
            db.add(shipment)
            by_tracking[move.after] = shipment
        links[move.item_id].shipment = shipment
    return "repaired", moves


async def audit(profile_id, scope, orders, *, apply):
    # Chromium's own profile lock prevents opening a second live copy.
    context, playwright = await session_manager._launch_chromium_context(
        str(profile_user_data_dir(profile_id, browser="chromium")),
        headless=settings.browser_headless, width=1280, height=800,
    )
    results = Counter()
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        for order_id, number in orders:
            try:
                url = f"https://www.amazon.com/your-orders/order-details?orderID={number}&disableCsd=missing-library"
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if looks_like_amazon_signin(page.url):
                    print(f"{number}: Amazon sign-in required. Audit stopped.", flush=True)
                    return 1
                await _inject_amazon_scripts(page)
                captured = await asyncio.wait_for(page.evaluate(r"""async () => {
                    const parser = OrderManagerAmazonDom
                    await parser.waitForOrderDetailReady(15000)
                    const order = parser.parseOrderDetailPage()
                    const ids = order.shipments.map(s => s.shipmentId.replace(/:package:\d+$/, ''))
                    if (ids.length > new Set(ids).size) {
                        await parser.enrichShipmentsWithTracking(order.shipments)
                    }
                    return {orderId: order.orderId, items: order.items, shipments: order.shipments}
                }"""), timeout=45)
                with SessionLocal.begin() as db:
                    result, moves = inspect_order(db, order_id, number, scope, captured, apply=apply)
                print(f"{number}: {result}", flush=True)
                for move in moves:
                    print(f"  Item {move.item_id}, qty {move.quantity}: {move.before} -> {move.after}", flush=True)
            except Exception as exc:
                result = "unchecked"
                print(f"{number}: unchecked ({type(exc).__name__})", flush=True)
            results[result.split(":", 1)[0]] += 1
    finally:
        try:
            await context.close()
        finally:
            await playwright.stop()
    print("Summary", ", ".join(f"{count} {result}" for result, count in results.items()), flush=True)
    if apply and not (results["repaired"] or results["assignments already correct"]):
        return 1
    return int(bool(results["unchecked"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--list-profiles", action="store_true", help="List Amazon browser profiles")
    selection.add_argument("--profile", type=int, help="Amazon browser profile ID")
    parser.add_argument("--order", help="Check a single Amazon order number")
    parser.add_argument("--apply", action="store_true", help="Repair one order (requires --profile and --order)")
    args = parser.parse_args()
    if args.list_profiles and (args.order or args.apply):
        parser.error("--list-profiles cannot be combined with --order or --apply")
    if args.apply and not args.order:
        parser.error("--apply requires --order")
    if args.order and not re.fullmatch(r"\d{3}-\d{7}-\d{7}", args.order):
        parser.error("--order must be an Amazon order number")

    with SessionLocal() as db:
        profiles = db.query(BrowserProfile).filter_by(retailer="amazon")
        if args.list_profiles:
            for profile in profiles.order_by(BrowserProfile.id):
                account = profile.store_account.name if profile.store_account else "(store-level)"
                print(f"{profile.id}\t{profile.store.name}\t{account}\t{profile.status}")
            return 0
        profile = profiles.filter_by(id=args.profile).one_or_none()
        if not profile or profile.status != "ready":
            parser.error("select a signed-in, idle Amazon profile")
        scope = dict(store_id=profile.store_id, store_account_id=profile.store_account_id)
        query = db.query(Order.id, Order.store_order_number).filter_by(**scope)
        if args.order:
            query = query.filter_by(store_order_number=args.order)
        orders = [(order_id, number) for order_id, number in query.order_by(Order.id)
                  if re.fullmatch(r"\d{3}-\d{7}-\d{7}", number or "")]
    if not orders or (args.order and len(orders) != 1):
        parser.error("could not find an unambiguous order for this profile")
    return asyncio.run(audit(args.profile, scope, orders, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
