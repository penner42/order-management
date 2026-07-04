# Order Manager Browser Integration

A browser extension that integrates order data from store/account pages into the Order Manager app. Works in Chrome and Firefox.

## Supported stores

- `Amazon` (`https://www.amazon.com/*` — order history and order detail pages)
  - Single-order import from an order detail page
  - Bulk import from the order history page (imports forward from the **current** page, then advances page-by-page)
  - Store Accounts in Order Manager should be named with your Amazon login email for auto-matching
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

## Installation (signed builds, recommended)

For personal use without loading unpacked every session, build signed packages once and install them.

**From the app:** open the **Extension** tab to download the latest signed Chrome/Firefox builds. The server rebuilds automatically on startup when extension source files change.

### One-time setup

```bash
cd browser-extension
npm install
npm run generate-key   # Chrome: creates .keys/chrome.pem and updates manifest.json
cp .env.example .env   # Firefox: add AMO API credentials (see below)
```

**Firefox AMO credentials** (required for signing; not for store listing):

1. Create a [Mozilla Add-ons developer account](https://addons.mozilla.org/en-US/developers/).
2. Accept the [Firefox Add-on Distribution Agreement](https://addons.mozilla.org/en-US/developers/addon/distribution/agreement/).
3. Generate an API key at [Manage API Keys](https://addons.mozilla.org/en-US/developers/addon/api/key/).
4. Put `WEB_EXT_API_KEY` and `WEB_EXT_API_SECRET` in `browser-extension/.env`.

### Build signed packages

```bash
cd browser-extension
npm run sign           # both browsers
# or individually:
npm run sign:chrome    # → dist/order-manager-<version>.crx
npm run sign:firefox   # → dist/order_manager_browser_integration-<version>.xpi
```

`web-ext` loads credentials from `.env` automatically when present.

### Install signed builds

**Chrome**

1. Open `chrome://extensions/`
2. Turn on **Developer mode**
3. Drag `dist/order-manager-<version>.crx` onto the page (or use **Pack extension** flow)

Keep `browser-extension/.keys/chrome.pem` backed up — the same key keeps the same extension ID and stored data across updates.

**Firefox**

1. Open `about:addons`
2. Click the gear icon → **Install Add-on From File…**
3. Select `dist/order_manager_browser_integration-<version>.xpi`

Firefox requires Mozilla-signed extensions even for personal use; the `unlisted` channel signs without publishing to AMO.

## Installation (unpacked, development)

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

### Amazon

Prerequisite: In Order Manager, create a Store named **Amazon** and Store Accounts named with each Amazon login email (e.g. personal and business).

Single-order import (order detail):

1. Log into the Amazon account you want to import from.
2. Open an order detail page (URL contains `order-details` or `orderID=`).
3. Wait for the page to finish loading.
4. Click **Import this order** in the extension popup.

Bulk import (order history, page-at-a-time):

1. Log into the desired Amazon account.
2. Open the order history page and navigate to the page where you want to **start** (e.g. page 3 of this year’s orders).
3. In the popup, choose **Pages to import forward (1–50)**.
4. Click **Start bulk import**.
5. The extension imports all orders on the current page, then advances to the next page until done.

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

- **Popup sections not showing**: open the correct page type (Amazon order history vs order detail, Walmart order detail vs orders list, Costco route) while you’re logged in.
- **Bulk import fails with missing config**: ensure **Production server base URL** is set and you’ve clicked **Login** in extension **Settings**.
- **Firefox issues**: the extension uses a page/extension messaging bridge. If something doesn’t work, try reloading the store page and re-opening the popup.
