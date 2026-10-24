# Automatically submit BFMR order numbers after import

## Summary

Match saved retailer orders to existing BFMR commitments and register their order numbers before tracking submission.

Run only for orders assigned to BFMR, controlled by a separate per-group setting that defaults off. Automatically submit unique product matches, review uncertain matches, and support commitments split across retailer orders.

## Matching and data

- Preserve imported line identity, retailer SKU/ASIN, and product URL through re-imports and shipment splits.
- Match retailer plus imported SKU/ASIN against BFMR’s verified `retailer_links[].retailer` and `identifier_id`. Titles and prices assist manual review only.
- Preserve BFMR reservation, purchase, and shipment IDs as opaque strings; parse quantity strings as integers.
- Aggregate shipment slices belonging to the same imported product before allocating quantity.
- Automatically select only one active reserved candidate with sufficient quantity. Missing identifiers, multiple candidates, and insufficient quantity require review.
- Use reservation status and fresh server validation for eligibility. Do not interpret `tracking_deadline` as the order-number deadline; recognize a reservation deadline only if BFMR supplies one explicitly.

## Submission and interfaces

- Add `order_number_submit_enabled` to buying-group models, schemas, and settings UI, available for BFMR.
- Persist a durable task in the import transaction and process it after commit through the existing background scheduler, independently of tracking schedules.
- Cover single/bulk imports, browser automation, re-imports, and later BFMR assignment of imported orders. Exclude personal orders and canceled/returning quantities.
- Add a purchase-specific client method: submit reservation ID, order number, and quantity with purchase, shipment, and tracking fields null.
- Serialize submissions per BFMR account. Refresh tracker state before writing and after each allocation to discover remaining reserved quantities.
- Persist allocations, resulting purchase IDs, outcomes, and retry state. Reconcile remote purchases before retrying after timeouts or restarts; unresolved outcomes require review.
- Retry transient failures with bounded backoff. BFMR failures must preserve successfully imported orders.
- Enforce the existing environment submission restriction on every write path.
- Add authenticated APIs for task/candidate listing, manual reservation-and-quantity allocation, and retry. Validate ownership and current availability before submission.

## Review and tracking

- Display submission state on affected orders and provide a group-level queue for unresolved matches and failures.
- Allow manual allocations across multiple commitments.
- Route subsequent tracking through persisted purchase allocations so multiple products sharing an order number reach the correct BFMR purchases.
- Purchase registration must not advance items to tracking-submitted status.
- Enabling automation does not submit historical orders automatically; provide an explicit scan action.
- Changes to already registered orders require review. Automatic remote cancellation and correction remain outside this change.

## Validation and rollout

- Test exact retailer identifiers, missing/ambiguous matches, quantity splits, multi-product orders, shipment splits, duplicate imports, concurrent tasks, and manual purchases already present remotely.
- Test partial failures, timeouts after remote success, restart recovery, transaction rollback, disabled settings, and preservation of imported orders.
- Run backend tests, frontend build, and browser smoke tests inside Docker using mocked writes and test-owned data.
- Before enabling automatic writes, inspect an active reservation read-only to confirm its quantity and null purchase fields. Current account queries contain no active reservations.
- Before implementation, save the approved plan to `docs/plans/bfmr-order-number-submission.md`.
