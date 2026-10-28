# BFMR API update

Implement the requested BFMR HTTP 400/409 handling and use the new exact tracker filters where appropriate.

1. Treat HTTP 400 and 409 as terminal provider rejections in the order registration worker, sending tasks to review without scheduling retries. Clear the sending journal for a definitively rejected POST while preserving it for uncertain transport or verification failures.
2. Support repeated `orders[]` and `trackings[]` parameters in the paginated tracker client, retaining them on every page and in diagnostic URLs.
3. Use exact order filters for post-registration verification and tracking submission. Preserve broad reads when registration needs unassigned commitments and when receiving must discover unknown receipts. Do not filter tracking submission by tracking number because purchases awaiting tracking have none yet.
4. Add regression coverage for terminal failures, filter encoding/pagination, and filtered callers. Run the backend unit suite inside the existing Docker development environment, without resetting shared data.
