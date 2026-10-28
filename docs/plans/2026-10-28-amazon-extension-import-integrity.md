# Amazon extension import integrity

Fix the confirmed problems from the read-only investigation:

1. Require a fresh response from the requested Amazon tab. Remove the popup's unvalidated global-capture fallback, check requested versus captured order IDs, and reject mismatched iframe and bulk captures.
2. Restrict purchased-item extraction to recognizable order item containers. Exclude recommendations and stop treating arbitrary product links or an order number alone as a complete order page.
3. Resolve the current account email afresh for each import. Remove cross-container/stale cache fallbacks, identify the selected account rather than the first account-switcher entry, and avoid unscoped email guesses from unrelated page data. Leave an unknown account unselected.
4. Use the same document scope for payment readiness and extraction. Wait for card digits when a card name hydrates first, support payment sections outside the selected order root, and preserve non-card payment methods.
5. Fail bulk detail capture explicitly instead of reporting success with fabricated list-summary details.
6. Add regression coverage for wrong-order fallback, order-ID mismatches, recommendations, account-switcher and stale-cache isolation, delayed and out-of-root payments, and failed bulk detail capture. Run relevant tests and frontend checks inside Docker without resetting or seeding the shared database.

Existing orders' saved account and payment associations will not be silently rewritten; historical bad imports require deliberate review. Preserve database, invoice, and browser-profile volumes.

During validation, the automatic signer started packaging an intermediate 0.1.35 build. Inspection also found that Chrome signing chooses the first ZIP in `dist`, allowing an older release to be signed under the current version. Correct the packager to use an explicit current-version ZIP and use 0.1.36 for the final source so the automatic signer cannot reuse the intermediate Firefox submission. Verify package contents against the final source.
