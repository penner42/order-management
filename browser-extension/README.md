# Order Manager Browser Integration

A browser extension that integrates order data from store order pages. Works in Chrome and Firefox.

## Stores

- **Walmart** – Reads order numbers from [walmart.com/orders](https://www.walmart.com/orders)

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

1. Visit [https://www.walmart.com/orders](https://www.walmart.com/orders) (while logged in)
2. Click the extension icon in your browser toolbar
3. In the popup, click **Get Order Numbers**
4. A browser alert shows the list of order numbers
