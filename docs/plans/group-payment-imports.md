# Additional buying-group payment imports

## Accepted plan

Implement read-only payment-history imports for BuyingGroup.com, USABG, and Deal & Runner through the existing Payment review workflow. Defer BFMR, payout requests, and scheduled imports. Source-paid means sent; bank receipt remains manual or Monarch-confirmed.

### Providers

- BuyingGroup.com: fetch all payments, Activity, receipts, and tracking rewards. Assume maximum available payment is requested. Reconstruct receipt balances across requests, using request creation as the boundary, explicit Activity references, and cash adjustments/revisions. Do not allocate by processing dates or count points as cash. Retain unexplained differences for review; never silently treat rewards as balancing amounts. Use payment created_dt and paid_dt for requested/sent.
- USABG: fetch all checks-requests and PO receiving details. Negate transaction values and aggregate explicit PO payment shares. One PO may span payments. Use added_date for requested and an issued check's date_insert for sent; approval alone is insufficient.
- Deal & Runner: fetch the authenticated runner's disbursements and full Transactions ledger. Import completed batches with completed individual payment status only. Pair payout debits to receiving credits by warehouse-item ID. Match debit batches by amount and timing, allowing delayed/multiple batches; ambiguous assignments require review. Use dateScheduled as the requested-date proxy, and the latest matched payout debit timestamp as sent. Keep dateUpdated (the completed UI's Lock Date) as context.

### Shared review and accounting

- Add normalized per-receipt payment shares, sanitized supporting references, and provider-specific review warnings.
- Support repeated receipt/item coverage across payments. Count physical quantities once across installments and enforce cumulative amount limits under existing locks/version/fingerprint controls.
- Preserve the first confirmed receipt snapshot as the accounting baseline. Unpaid baseline amounts remain outstanding; actual source revisions use signed corrections. Existing confirmed payments retain their totals through migration.
- Display receipt total, previously allocated amount, payment share, and remaining balance. Preserve manual review edits across refreshes and flag changed source allocations.
- Require complete, stable pagination and identities; failures preserve prior evidence. Preserve existing Parsefile behavior and portal UTC conventions for dates without offsets; retain source date context.

## Investigation evidence (2026-10-20)

- BG: 103 payments, 101 paid and 2 requested. Activity's 101 processed payment references and amounts match payment history. Simple request windows reconcile 78/103 totals; 25 need carried balances/eligibility reconciliation. Adding current commissions/tracking rewards closes none of those 25 differences. Several differences cancel across requests. Cash referral events are later than these discrepancies. Points remain separate.
- USABG: 9 requests, 8 approved and 1 pending. Negated transaction sums match issued payouts. A PO appears in two payouts, requiring partial payment coverage.
- D&R: 45 disbursements, 43 completed. All 584 payout debits pair with equal/opposite receiving credits by warehouse-item ID. Lock-date windows match 41/43; a February payout posted eight days late in two batches. Accounting for those batches reconciles all 43. Disbursement detail endpoints return 403 for this account.

## Validation

Use sanitized fixtures for pagination, expiry, malformed/unknown responses, missing evidence, and failure preservation. Cover BG carryover/revisions/rewards; USABG split POs and signs; D&R delayed/split/ambiguous batches and date semantics. Test installment amounts, physical quantities, corrections, concurrent confirmations, and migration compatibility. Run backend tests, frontend build, and relevant browser tests inside Docker with isolated test data. Preserve development database and volumes.

## Progress

- Implemented the three providers, provider-specific warnings/evidence, installment accounting, receipt balance UI, and migration `078`.
- Applied `078` to the Docker development database; all application services are healthy. Existing volumes and payments were preserved.
- Verified live read-only fetches: BG 103 payments/991 receipts, USABG 9 requests/53 POs, D&R 43 completed payouts/614 receiving credits. All 43 D&R payouts obtain exact debit-batch matches. USABG's eight issued payouts provide explicit PO shares.
- BG supplies 79 selections, 78 with exact monetary shares; unresolved opening/held balances and dependent suggestions retain review warnings. BG review creation was exercised in an isolated in-memory database, with no local payments confirmed.
- Additional reward check: BG Activity usually includes the tracking reward in its receipt cash credit (for example, 288.00 + 0.05 = 288.05). Normalize that reward once and use local item reward values in BG payout capacity; do not add commission as an unexplained balancing amount.
- Validation passed: full backend suite (256 tests), final provider suite (14 tests), final payment/installment/concurrency regressions (37 tests), frontend production build, browser-test type check, and 10 payment browser tests. The browser runner initially recreated the backend during testing; the full suite was rerun successfully afterward. An older Monarch test fixture was completed with its missing required scheduling fields for the type check.
- Remaining operational work is ordinary user review: run Check payments, prepare missing received items/rewards, and resolve displayed ambiguities before confirmation. No production deployment, payout request, or recurring import was performed.
