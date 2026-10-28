# Payments driven by buying-group data

## Summary

Replace manual payment creation and per-payment import confirmation with automatic synchronization. Buying-group data controls payment identity, total, requested date, sent date, and source status. Item matching happens separately and can be adjusted manually.

Cover Parsefile, BuyingGroup.com, USABG, and Deal & Runner. BFMR remains explicitly unsupported.

## Payment ledger and matching

- Import payments requested **or** sent on or after January 1, 2026. Retain older source history as reconciliation context.
- Store the group’s payment amount independently of item allocations. Payment totals and Monarch matching use that authoritative amount, even when purchases are missing.
- Show each payment’s linked items, historical portions, explained adjustments, and unresolved balance. These must reconcile to the source total; differences never become automatic bonuses.
- Support 2026 payments containing 2025 purchases without creating historical orders. Label portions historical only when evidence or an explicit manual classification supports it.
- Automatically apply clear receipt/item matches using existing provider evidence and installment rules. Ambiguous or missing matches flag the payment for attention without preventing its import.
- Re-run automatic matching after relevant receiving/order changes. Preserve manually edited allocations until explicitly reset to automatic matching.
- Allow explained changes to item inclusion and allocation amounts. Enforce buying-group, quantity, and cumulative allocation limits.

## Synchronization and archives

- Reuse one synchronization service for **Sync now** and the existing per-group scheduler. Add a payment-sync setting, enabled by default for supported, configured groups, using their existing interval and global scheduler controls.
- Update source-owned fields automatically. Preserve allocation overrides and flag conflicts caused by source changes.
- Retain source revisions and adjustment history. Adapt existing receipt corrections so they affect item accounting without adding money twice to authoritative payment totals.
- Extend Parsefile fetching to discover and read its actual archive mechanism, using authenticated, read-only portal inspection before implementing archive requests.
- Merge current and archived payment, activity, and receiving evidence. Preserve identity across archival moves; distinguish reused IDs where necessary. Automatically fetch prior-year archives and older partitions needed by referenced receipts.
- An unavailable archive or incomplete fetch preserves previously imported evidence and reports incomplete coverage. Missing or voided source payments remain auditable and flagged; they are never silently deleted.
- Preserve existing provider-specific matching safeguards, pagination checks, version checks, and concurrency locks.

## Interface, APIs, and bank receipt

- Make Payments the primary imported ledger, with group filters, sync status, allocation details, and a **Needs attention** view. Replace the confirmation queue with adjustment and exception handling.
- Remove manual payment creation and source-field editing from the UI and reject those operations through the API. Expose versioned allocation adjustments and reset-to-automatic actions.
- Extend payment responses with authoritative amount, allocation breakdown, matching state, source revision/status, and synchronization issues. Extend group settings with payment-sync enablement and last-run outcome.
- A payment is received only through a Monarch deposit link. Remove standalone received-date entry.
- Automatically link unique exact-total Monarch matches under the existing sender/date rules. Check competing payment/deposit combinations before linking; ambiguous cases require manual selection.
- Derive received dates from linked deposits. If a later source change invalidates a confirmed amount, retain the audit record, flag the receipt conflict, and require unlinking/relinking before considering it reconciled.

## Migration and validation

- Upon implementation approval, save this approved plan under `docs/plans/` before changing code.
- Preserve existing payments, coverage, corrections, and Monarch links. Match legacy payments to source records only when identity and accounting evidence produce a unique match.
- Keep unmatched legacy payments visible in a separate reconciliation section and exclude them from imported-ledger totals to prevent double counting. Never delete them automatically.
- Test mixed-year payments, missing purchases, historical classification, installment coverage, later rematching, preserved overrides, source revisions, voids, archive moves, reused IDs, incomplete fetches, and repeated/concurrent syncs.
- Test unique and competing Monarch matches, manual deposit linking, receipt conflicts after source changes, and migration without duplicate payments or totals.
- Run backend tests, frontend build, and payment/Monarch browser tests inside Docker using isolated test data. Preserve shared database and profile volumes.
