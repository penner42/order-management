# Costco bundled software payouts

Approved implementation:

- Add a terminal item status labeled “No payment necessary” rather than creating a fictitious received payment.
- Recognize separate Costco software lines (including Microsoft 365 and McAfee) bundled with another product. Standalone software and hardware bundle descriptions must retain normal handling.
- Set bundled software payouts to $0 and apply the new status on import and re-import, overriding supplied payouts. Preserve purchase prices and order discounts.
- Preserve cancellation/return workflows and existing payment links during re-import.
- Expose the status in order displays and filters, and exclude it from payment allocation workflows.
- Add focused regression tests and run backend tests and the frontend build inside Docker. Apply the schema migration to the development environment without resetting or seeding shared data.
