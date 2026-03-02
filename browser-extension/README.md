# Order Manager Browser Integration

A browser extension that integrates order data from store order pages. Works in Chrome and Firefox.

## Stores

- Store order pages supported via content scripts (e.g. Costco orders)

## Installation (Unpacked)

### Chrome

1. Open `chrome://extensions/`
2. Turn on **Developer mode**
3. Click **Load unpacked**
4. Select the `browser-extension` folder

### Firefox

1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select any file in the `browser-extension` folder (e.g. `manifest.json`)

## Usage

1. Visit a supported store's orders page (while logged in)
2. Order numbers (and related metadata when available) are injected into each order card automatically
3. For order details, open an order and use the popup: **Get Order Details** → **Send to Order Manager**
