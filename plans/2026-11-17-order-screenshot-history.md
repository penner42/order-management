# Cropped order screenshot history

## Summary

Save an initial screenshot for each order, then a dated version only when meaningful order information changes. Capture the retailer’s actual order content, cropped to exclude unrelated page elements.

## Capture and change detection

- Support Amazon, Costco, and Walmart browser imports and scheduled checks.
- Include order number/date, items, quantities, prices, totals, shipping address, payment summary, shipment status, tracking, and delivery estimates.
- Exclude site navigation, ads, recommendations, footers, and floating widgets using retailer-specific capture regions.
- Compare normalized order information with the latest saved screenshot’s information. Ignore layout, images, capture timestamps, URLs, and barcode enrichment.
- Save changes back to an earlier state as a new version—for example, canceled → active → canceled.
- Capture while the matching order details are visible, before navigating away. Costco’s API-only fallback must not capture an unrelated page.
- If the correct crop cannot be verified, log the failure and continue importing. Retry on the next check without advancing the screenshot baseline.

## Storage and viewing

- Add screenshot-history records containing the order, capture time, comparison data, change summary, and image filename.
- Store PNG files beneath the existing persistent invoice directory, in a separate screenshots folder.
- Stage captures until the import is applied; clean up abandoned captures. Recheck the latest version when saving to prevent duplicate history from overlapping jobs.
- Add authenticated endpoints to list an order’s versions and retrieve its images.
- Add a “Screenshot history” action on orders, opening dated versions with change summaries, image previews, and downloads.

## Validation and defaults

- Test initial capture, unchanged checks, meaningful changes, reversals, overlapping checks, and separation between retailer accounts.
- Verify crops for all three retailers, including long orders, multiple shipments, loading states, and missing capture regions.
- Verify failed captures preserve existing history and do not fail order imports.
- Run backend tests, frontend build, and browser smoke tests inside Docker using isolated test data.
- Retain all saved versions until the order is deleted. Existing orders receive their first screenshot when next visited by automation; earlier history cannot be reconstructed.
- Before implementation, save this approved plan under the project’s existing `plans/` convention.
