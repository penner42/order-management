# Reuse unchanged BuyingGroup receipt details

- Remove the paid or fully scanned requirement for reusing receipt details from a previous successful check for the same BuyingGroup account.
- Keep the existing checks for an unchanged receipt summary, a usable `modified_dt`, and complete, verified cached details whose quantities add up to the summary quantity.
- Fetch details for new or changed receipts and for receipts with missing revisions or missing, incomplete, or partially confirmed details. Keep fetching the current receipt, return, and tracking rewards lists during a receiving check.
- Update the existing cache regression test to cover reuse of an unchanged unpaid and unscanned receipt and refetching after a summary change. Run the backend tests in Docker.
