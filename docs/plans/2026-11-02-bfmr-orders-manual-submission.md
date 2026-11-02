# Submit BFMR order numbers from the Orders page

## Summary

Add an order-level preview and manual submission flow that works before tracking exists and independently of automatic submission settings. Keep dev preview-only.

## Changes

- Fix numeric BFMR `order_deadline` handling: positive values represent remaining seconds; zero or negative values are expired. Preserve explicit-date and absent-deadline handling.
- Add **BFMR order number** beside the buying-group selector for BFMR orders, even when no registration task exists.
- Open an inline dialog showing the saved order number, products, quantities, current registration status, and live commitment matches. Prefill a unique exact match; allow manual commitment selection and quantity splits.
- Require explicit selection of products to submit. Exclude canceled, returning, lost, and `no_payment_necessary` items.
- Provide **Submit order number** in the dialog when environment submissions are enabled. In dev, allow preview and explain why submission is unavailable.
- Display success, partial success, and review-required outcomes on the order without navigating to settings. Refresh saved status after submission.

## Backend behavior

- Add authenticated order-specific preview and submission endpoints under the existing BFMR order-number router. Validate order ownership and BFMR assignment.
- Preview reads local products and live commitments without creating tasks or changing settings.
- Manual submission creates or reuses durable product tasks and executes the existing registration service immediately, without requiring automatic submission or scheduled jobs to be enabled.
- Reuse account locking, fresh availability checks, purchase reconciliation, and the durable write journal. Prevent duplicate submissions and preserve confirmed allocations after partial failures.
- Keep automatic processing controlled by its existing setting. Manual retries use the same order dialog; uncertain outcomes require reconciliation before another write.
- Register purchases with no tracking number and leave item tracking-submission status unchanged.

## Validation

- Cover countdown deadlines and the observed two-unit Costco product matching a four-unit commitment.
- Test manual submission with automation and scheduling disabled but environment submissions enabled.
- Test dev preview access and server-side rejection of writes.
- Cover quantity splits, duplicate clicks, changed commitments, ownership, partial failures, uncertain responses, and excluded bundled software.
- Run relevant backend tests, frontend build, and focused Orders-page browser tests inside Docker.

## Defaults

Keep dev submissions and scheduled jobs disabled. Preserve the group review page as an additional interface. On implementation approval, save this replacement plan under `docs/plans/` before changing code.
