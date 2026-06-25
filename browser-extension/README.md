# Order Manager Browser Integration

A browser extension that integrates order data from store/account pages into the Order Manager app. Works in Chrome and Firefox.

## Supported stores

- `Walmart` (`https://www.walmart.com/*`)
  - Order details import (single order)
  - Bulk import from the orders list page (multiple orders)
- `Costco` (`https://www.costco.com/myaccount/*`)
  - Bulk import from the “orders and purchases” page
  - Single-order import from an order detail page
  - On the Costco “orders and purchases” page, it also injects a “Tracking # …” line next to each “Track My Package” link (best-effort)
- `USABG` (`https://app.usabuying.group/*` / `https://*.usabuying.group/*`)
  - Load Buyer POs (prints the fetched data in the popup)
- `Buying Group` (`https://buyinggroup.com/*` / `https://*.buyinggroup.com/*`)
  - Load Orders (prints the fetched data in the popup)

## Installation (unpacked)

### Chrome

1. Open `chrome://extensions/`
2. Turn on **Developer mode**
3. Click **Load unpacked**
4. Select the `browser-extension` folder

### Firefox

1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select any file in the `browser-extension` folder (e.g. `manifest.json`)

## Configure Order Manager (required)

1. Click the extension icon on any supported store page.
2. Click **Settings**.
3. Set the **Production server base URL** (required).
4. Click **Login** under Authentication.
5. (Optional) Enable **Dev** in Settings and configure the dev base URL.
6. Back in the popup, use **dev server** toggle to switch between `prod` and `dev`.

Note: Bulk import flows open an Order Manager “bulk import review” page after capturing/normalizing orders.

## Usage

### Walmart

Order details (single order):

1. Open a Walmart order detail page (the popup detects URLs like `/orders/<...>` or `/order-details`).
2. Click **Get Order Details**.
3. Click **Send to Order Manager** (this opens the bulk import review page for that single order payload).

Bulk import (orders list):

1. Open the Walmart orders list page (`https://www.walmart.com/orders`).
2. In the popup, choose **Pages to import (1–50)**.
3. Click **Start bulk import**.
4. A dedicated bulk import tab opens and runs in the background; this page closes when finished.

### Costco

Bulk import (orders and purchases):

1. Open the Costco “orders and purchases” page (the popup detects the `ordersandpurchases` route).
2. Click **Start bulk import**.
3. The bulk import tab uses the Costco page’s existing GraphQL response (it does not do page navigation yet).
4. If the tab reports **“No payload captured”**, refresh the Costco orders page and try again.

Single-order import (order detail):

1. Open the Costco order detail page (the popup detects the `orderdetails` route).
2. If needed, refresh the page once so the extension can capture the GraphQL response.
3. Click **Import this order**.
4. If you see **“No saved Costco order details captured yet”**, refresh the order detail page and try again.

### USABG (Buyer POs)

1. Visit a page under `https://app.usabuying.group/` (or another `*.usabuying.group` page that calls `api.usabuying.group`).
2. In the popup, click **Load Buyer POs**.
3. The fetched PO data is shown directly in the popup.

If it says no token is found, visit a page that calls `api.usabuying.group` first, then refresh and retry.

### Buying Group (BG orders)

1. Visit a page under `buyinggroup.com` / `*.buyinggroup.com`.
2. In the popup, click **Load Orders**.
3. The fetched orders data is shown directly in the popup.

If it says no BG token is found, visit the BG site and refresh, then try again.

## Troubleshooting

- **Popup sections not showing**: open the correct page type (Walmart order detail vs Walmart orders list vs Costco route) while you’re logged in.
- **Bulk import fails with missing config**: ensure **Production server base URL** is set and you’ve clicked **Login** in extension **Settings**.
- **Firefox issues**: the extension uses a page/extension messaging bridge. If something doesn’t work, try reloading the store page and re-opening the popup.
