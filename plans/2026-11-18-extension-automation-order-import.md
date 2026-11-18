# Import a retailer order with browser automation

## User flow

- Add **Import with browser automation** alongside the existing extension import on Amazon, Walmart, and Costco order detail pages.
- Read the order number from the current page. When page data provides an account email, use a unique match to a configured subaccount name as the suggested profile. Show the store and subaccount for confirmation. If there’s one matching retailer profile, show it directly. If there are several and no unique suggestion, ask the user to choose. Use only account data tied to the current tab and order.
- On confirmation, start the job on the extension’s selected Order Manager server and open a new foreground tab. The retailer tab stays available. The Order Manager tab shows progress and loads the existing Import Review page when capture finishes. The job keeps running if the user closes the popup, navigates the retailer tab, or refreshes the progress page.

## Implementation

- Reuse the authenticated browser profile list and job status API. Add `POST /api/browser-profiles/{profile_id}/import-order` with `{ retailer, order_number }`, returning the existing job-start response. The profile supplies the store and optional subaccount. Validate the order number and retailer match on the server, and use the shared profile queue to prevent overlapping imports or login sessions.
- Add a manual single-order job that always stages for review. Reuse the targeted Amazon and Walmart capture paths. For Costco, open the requested detail route using the automation session’s own app ID, normalize the matching detail response with the shared Costco parser, and enrich UPCs. If direct capture fails, use the existing targeted history lookup within the profile’s configured page limit and require a matching detail response.
- Verify that the capture belongs to the requested retailer and order before staging it with the selected profile’s ownership and any screenshot token. Use the existing screenshot verification and history rules. The job appears in the import log as a single-order import and leaves full and unshipped check timestamps alone.
- Add an authenticated progress route keyed by job ID. Preserve it through app sign-in and use the completed job’s token to open Import Review on the same server. Show actionable errors for missing profiles, busy profiles, retailer sign-in, failed capture, and jobs lost after a backend restart.

## Verification and rollout

- Add focused tests for order identity, profile selection, busy-profile rejection, review staging with screenshot and ownership metadata, and failure without staging. Cover Costco direct capture and targeted fallback with mocked retailer responses.
- Run backend tests, the frontend build, and browser smoke tests in Docker. Exercise the new action in Chrome and Firefox with seeded test fixtures or mocked jobs, including navigating away from the retailer page while capture runs. Check that the existing extension import still opens review.
- No database migration is required. Use the project’s extension build and signing workflow for distribution after verification. On implementation approval, save this full plan to `plans/2026-11-18-extension-automation-order-import.md` before editing source.
