# BG shipment tracking priority

Approved implementation plan (2026-10-27):

- For BuyingGroup.com receiving matches, prioritize receipt candidates found through an item's shipment tracking numbers (including existing shipment aliases) over candidates found only through the Walmart order-number fallback.
- Identify the fallback by comparing a candidate identifier to the item's Walmart store order number; preserve existing behavior for other stores.
- Use Walmart order-number candidates when no eligible shipment-tracking product candidate is available. Keep existing product validation, quantity allocation, and applied-receipt protections.
- Add regression coverage for the reported mixed pink/blue laptop order, fallback matching, and already-scanned receipt allocation.
- Run the relevant backend tests inside Docker. Do not modify existing order or receipt records.
