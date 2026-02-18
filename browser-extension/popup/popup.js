document.getElementById("getOrders").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const pageCount = parseInt(
    document.getElementById("pageCount").value || "1",
    10
  );
  const safePageCount = Math.max(1, isNaN(pageCount) ? 1 : pageCount);
  try {
    await chrome.tabs.sendMessage(tab.id, {
      action: "getOrderNumbers",
      pageCount: safePageCount,
    });
  } catch {
    alert("Order Manager: Open walmart.com/orders first, then try again.");
  }
});

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
          items.forEach(({ name, price, quantity, trackingNumber }) => {
            let meta = `${escapeHtml(price)} · Qty ${quantity}`;
            if (trackingNumber) meta += ` · ${escapeHtml(trackingNumber)}`;
            html += `<div class="item"><div class="item-name">${escapeHtml(name)}</div><div class="item-meta">${meta}</div></div>`;
          });
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
