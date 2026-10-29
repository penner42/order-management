# Focused payment linking workspace

## Summary

Replace the inline allocation form with a dedicated workspace for one payment. Show buying-group receipt rows on the left and available local items grouped by order on the right. Users select a receipt, select items, and click **Link selected**. Changes remain a draft until saved.

Keep the Payments page as a compact overview with an **Open payment** action. Other payments disappear while working in the workspace.

## Workspace behavior

- Add `/payments/:paymentId/link`, with a back link preserving overview filters and previous/next navigation through that filtered payment list.
- Keep payment reference, group, dates/status, source total, linked amount, and unresolved balance visible in a sticky header. Show source warnings and deposit conflicts for this payment only.
- **Left pane — Payment items:** show receipts identified as belonging to this payment, plus receipts used by existing allocations. Each row shows description, reference, quantity, amount, and linked local items. Selecting a receipt establishes the target for linking; linked items have edit and unlink actions.
- **Right pane — Available order items:** show received items from the same buying group with remaining allocatable balances, including partially covered items. Group them under expandable order headers with store, order number, and order link. Allow item selection and selecting eligible items within an order.
- Search local items by description, order number, receipt reference, and tracking number. Rank exact receipt/tracking matches first without hiding other eligible items.
- Link selected items to the selected receipt. Prefill each item’s full eligible quantity and remaining payout when the complete selection fits item, receipt, and payment limits. Otherwise open a compact quantity/amount editor; require explicit values rather than silently prorating or truncating.
- Update both panes and draft totals immediately. Unlinking releases capacity in the draft. Items with remaining capacity stay available; fully allocated items disappear from available choices.
- Provide **Add group receipt** for payments with missing or incomplete receipt associations. This explicitly selects another current receipt from the same group and provider.
- Put historical amounts, explained adjustments, and reset to automatic matching in a collapsed **Other adjustments** section. Keep corrections visible as read-only entries.
- Stack panes on narrow screens with clear target selection and accessible checkbox/button interactions.

## Data and saving

- Add an authenticated `GET /payments/{payment_id}/linking-workspace` endpoint. Return the payment and allocation version, associated and selectable group receipts, existing allocations, and structured order/item details.
- Return receipt baselines, coverage by other payments, item payout capacity, and receipt/item quantity commitments needed to calculate draft availability. Exclude the current payment’s editable allocations from committed balances; account separately for its corrections. Preserve installment quantities and signed correction behavior.
- Build this response using batched queries so query count does not grow per item or receipt. Keep the existing group evidence endpoint compatible.
- Reuse the versioned allocation save and reset endpoints. Retain all backend group/provider, received-status, quantity, balance, and concurrency checks. No database migration or payment-status changes.
- Use integer cents for client calculations and decimal strings in requests. Prevent duplicate receipt/item allocations.
- **Save changes** persists the complete draft and keeps the workspace open. Generate an editable explanation summarizing routine link/unlink changes; historical amounts and adjustments require a user-written explanation.
- Guard internal navigation and browser exit when changes are unsaved. Confirm before resetting manual allocations.
- Preserve drafts on failed saves. On a stale-version response, offer an explicit reload that warns it will discard the draft; never overwrite or automatically resubmit stale changes. Evidence load failures have a retry action.

## Validation and defaults

- Backend tests cover payment-scoped evidence, group/provider boundaries, current-allocation capacity, corrections, partial payments, installment quantities, missing receipts, and bounded query counts.
- Browser tests cover isolation to one payment, search and order selection, linking/editing/unlinking, draft totals, save/reset, navigation guards, retry and stale-save behavior, and desktop/mobile layouts. Retain existing source-field and received-status protections.
- Run the Docker frontend build, backend unit suite, and browser smoke suite. Tests create and clean up their own data and preserve shared volumes.
- Default to explicit save, click-to-link, and partially covered items remaining available. Legacy payments remain read-only in their existing separate view.
- After implementation approval, save this plan under the established `docs/plans` naming convention before making changes, and report its path.
