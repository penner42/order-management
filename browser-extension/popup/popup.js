/** Order details from the content script (getOrderDetails). Stored after a successful fetch so "Send to Order Manager" can use it. */
let lastOrderDetails = null;

document
  .getElementById("getOrderDetails")
  .addEventListener("click", async () => {
    const resultsEl = document.getElementById("results");
    const sendBtn = document.getElementById("sendToApp");
    resultsEl.style.display = "block";
    sendBtn.style.display = "none";
    lastOrderDetails = null;
    try {
      const [tab] = await chrome.tabs.query({
        active: true,
        currentWindow: true,
      });
      if (!tab?.id) {
        resultsEl.innerHTML = `<span class="error">No active tab.</span>`;
        return;
      }
      resultsEl.innerHTML = `<span class="loading">Hard refreshing page…</span>`;
      await hardRefreshTab(tab.id);
      resultsEl.innerHTML = `<span class="loading">Extracting order details…</span>`;
      const response = await chrome.tabs.sendMessage(tab.id, {
        action: "getOrderDetails",
      });
      if (response.error) {
        resultsEl.innerHTML = `<span class="error">Not on an order detail page (orders/XXXX/...).</span>`;
      } else if (response.details) {
        lastOrderDetails = response.details;
        sendBtn.style.display = "block";
        const {
          orderNumber,
          items,
          address,
          paymentMethod,
          account,
          orderDate,
        } = response.details;
        let html = `<div class="order-number">Order ${orderNumber}</div>`;
        if (orderDate) {
          html += `<div class="order-date">${escapeHtml(orderDate)}</div>`;
        }
        if (account) {
          html += `<div class="account">${escapeHtml(account)}</div>`;
        }
        if (address) {
          html += `<div class="address">${escapeHtml(address)}</div>`;
        }
        if (paymentMethod) {
          html += `<div class="payment">${escapeHtml(paymentMethod)}</div>`;
        }
        if (items.length === 0) {
          html += "<div>No items found.</div>";
        } else {
          let orderTotal = 0;
          items.forEach(({ name, price, quantity, trackingNumber }) => {
            const qty = Math.max(0, quantity ?? 1);
            const unitCost = parsePrice(price);
            const lineTotal = unitCost * qty;
            orderTotal += lineTotal;
            let meta = `Unit ${escapeHtml(price)} × ${qty} = $${lineTotal.toFixed(2)}`;
            if (trackingNumber) meta += ` · ${escapeHtml(trackingNumber)}`;
            html += `<div class="item"><div class="item-name">${escapeHtml(name)}</div><div class="item-meta">${meta}</div></div>`;
          });
          html += `<div class="order-total">Order total (calculated): $${orderTotal.toFixed(2)}</div>`;
        }
        resultsEl.innerHTML = html;
      }
    } catch {
      resultsEl.innerHTML = `<span class="error">Open a Walmart order detail page (orders/XXXX/...) first, then try again.</span>`;
    }
  });

document.getElementById("sendToApp").addEventListener("click", () => {
  if (!lastOrderDetails) return;
  const baseUrl = "http://localhost:5173";
  try {
    const json = JSON.stringify(lastOrderDetails);
    const encoded = btoa(unescape(encodeURIComponent(json)));
    const url = `${baseUrl}/import-preview#${encoded}`;
    chrome.tabs.create({ url });
  } catch (e) {
    alert("Order Manager: Could not encode order data. Try again.");
  }
});

function hardRefreshTab(tabId) {
  return new Promise((resolve) => {
    const timeoutMs = 60000;
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, timeoutMs);
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.reload(tabId, { bypassCache: true });
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

/** Parse price string to number for display-only totals (not saved). */
function parsePrice(s) {
  if (s == null || String(s).trim() === "") return 0;
  const n = parseFloat(String(s).replace(/[^0-9.-]/g, ""));
  return Number.isNaN(n) ? 0 : n;
}
