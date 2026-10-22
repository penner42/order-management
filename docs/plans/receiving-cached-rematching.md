# Rematch cached receipts whenever Receiving opens

## Summary
Separate provider fetching from local matching for Parsefile, BuyingGroup.com,
BFMR, USABG, and Deal & Runner. Receipt checks populate a complete durable cache;
Receiving rematches that cache against saved local orders on navigation and tab return.

## Backend
- Store versioned complete provider matching inputs, provider identity, and fetch time
  separately from the pending review queue. Preserve decimals and unavailable details;
  never store credentials. Cache before local filtering and diagnostic truncation.
- Share matching between fresh checks and cached reviews. Successful empty checks
  replace the cache; failed checks retain it. Applying/pruning never removes it.
- Regenerate pending reviews on GET without provider calls, authentication, order
  changes, or updating last-check timestamps. Serialize per-group queue operations.
- Preserve and recompute valid manual assignments. Invalidate whole assignments when
  items disappear, change groups, become incompatible, or no longer total the receipt.
- Keep legacy queues until a successful check; ignore caches with changed provider
  identity. Keep created_at as fetch time and add optional rematched_at.

## Frontend
- Refresh on entry and deduplicated focus/visibility return. Prevent stale responses.
- Defer background refresh while edits, dialogs, or mutations are active. Offer
  explicit reload explaining discarded edits. Retain data on background errors.

## Validation
- Test each provider: fetch once, edit local orders/tracking, rematch with provider
  calls forbidden. Cover allocation, splits, applied receipts, manual links, deleted
  items, empty/failed checks, legacy queues, and complete cached inputs.
- Browser tests cover navigation/tab return, dirty edits, reload, and errors.
- Run backend tests, frontend build, and browser smoke tests in Docker. Preserve
  shared volumes; tests own and clean their data.

## Assumptions
Current orders means saved database state. New warehouse data still requires a
receipt check. Preserve existing receipt-detail behavior and shared volumes.
