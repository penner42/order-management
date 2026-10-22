# Find receipt matches across buying groups

## Summary

Add **Search all buying groups** to the receipt-link dialog. Show likely tracking matches from other groups and let users open the order to correct its group before linking.

## Changes

- Keep searches limited to the receipt’s group by default. Add an independent **Search all buying groups** checkbox; retain **Show lines with receipts**.
- Extend the existing candidates endpoint with `include_all_groups=false` and return each order’s group ID and name. All-groups searches also include orders without an assigned group.
- Display the current group on every candidate. For another group or an unassigned order, disable selection and show **Open order to correct group**, using the existing `/?order_id=…` view in a new tab.
- Explain beside that action: “The buying group applies to the whole order. Review its other lines before changing it.”
- In the dialog, check for exact shipment or receipt-tracking matches outside the current group, independently of the search and receipt-visibility filters. Show a **Possible match in another group** hint with the matching lines and their group names. Treat these as suggestions requiring review.
- Add **Refresh order lines** so users can return after correcting the order. Refresh clears selected candidates and reloads both results and suggestions.
- Preserve backend rejection of cross-group links and existing receipt, quantity, and payment protections.

## Validation

- Backend tests cover default group filtering, all-groups searches, unassigned orders, existing receipt filters, and tracking suggestions across groups.
- Browser tests cover opening the correct order, refreshing after its group changes, and linking a payment-requested line without changing its payment status.
- Verify another-group candidates cannot be selected or linked, including when the order’s group changes after selection.
- Run backend tests, frontend build, and browser smoke tests inside Docker. Tests create and clean up their own data.

## Assumptions

- Group correction remains in the Orders screen, as selected.
- Suggestions use exact tracking matches with whitespace and case normalization; description similarity alone does not trigger a hint.
- No automatic reassignment, payment changes, database migrations, or changes to automatic receipt matching.
