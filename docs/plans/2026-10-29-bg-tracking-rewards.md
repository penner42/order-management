# BG rewards counted once per tracking

## Summary

Store BG’s reported reward at tracking level and show it beside the tracking number on Orders. Keep internal line allocations for receipt reconciliation, adding each allocation once rather than multiplying it by quantity.

Use BG’s reported amount, including $0.10 or $0.15. Repair existing orders and pending reviews while preserving confirmed payments.

## Implementation changes

- Add persistent tracking rewards scoped to the buying group and provider account. Resolve primary and alternate tracking identifiers to the same reward; duplicate shipment records must not duplicate it.
- Populate rewards from successful BG receiving/payment fetches. Preserve saved amounts when rewards are unavailable; distinguish an explicit zero from a missing reward.
- Retain `Item.reward` as an internal allocated line total for compatibility. Calculate payable as `unit payout × quantity + allocated reward`.
- Align receiving allocations with payment evidence: assign the reward to the receipt BG credits, then divide it across that receipt’s confirmed item lines using deterministic cent rounding. All shares must sum to the tracking reward. Unmatched quantities remain unresolved rather than shifting their share onto matched lines.
- Reuse this allocation logic for automatic receiving, manual receipt links, historical repair, and line splits. Refresh review fingerprints when reward allocations change.

## Orders display and API

- Remove the item-level Reward column. Show **Scan reward $0.05** beside the first visible occurrence of the associated tracking in each order.
- Add tracking-reward summaries to order responses and frontend types, including the total amount, associated item IDs, and whether the tracking spans multiple orders.
- For a tracking shared across orders, show the same tracking total in each relevant order with a “shared across orders” label. These badges are informational and must not be summed as separate rewards.
- Resolve summaries on the backend across all associated lines so filtering and pagination cannot change the displayed amount. If warehouse tracking differs from shipping tracking, label the warehouse identifier explicitly.

## Existing data and rollout

- Save the approved plan under `docs/plans/` before implementation, following project instructions.
- Add the schema migration and an idempotent repair command with preview and apply modes.
- Backfill from account-matched cached BG rewards and receipt payment evidence. Recompute line allocations without changing product prices, quantities, receipt assignments, or statuses. Report missing or conflicting evidence instead of guessing.
- Recalculate automatic pending reviews. Preserve manual review choices and flag conflicts for review.
- Preserve confirmed payment amounts, allocations, and receipt confirmations, including avoiding automatic rematching side effects during repair. Report discrepancies against corrected rewards.
- Run preview, apply, and a second preview in development; provide the same commands for production, which runs separately. Report affected orders, reward changes, unresolved cases, and payment discrepancies.

## Validation

- Verify quantity 3 at $20 with a $0.05 tracking reward produces $60.05.
- Cover shared tracking across lines/orders, multiple receipts, alternate identifiers, duplicate shipments, partial receiving, manual linking, splits, zero rewards, and unavailable reward data.
- Verify cent-exact allocation totals and receipt-level agreement with payment evidence.
- Verify repair reruns make no further changes and confirmed payments remain unchanged.
- Browser-test the tracking badge, shared-order labeling, filters, pagination, and removal of the Reward column.
- Run backend tests, frontend build/type checks, and browser smoke tests inside Docker, preserving existing volumes.
