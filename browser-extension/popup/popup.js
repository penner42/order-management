/** Order details from the content script (getOrderDetails). Stored after a successful fetch so "Send to Order Manager" can use it. */
let lastOrderDetails = null;

const USABG_API_URL =
  "https://api.usabuying.group/buyers/pos?limit=20&start=0";
const BG_ORDERS_URL = "https://api.prod.buyinggroup.com/v1/receipt/get_analytics";
const BG_TOKEN_URL = "https://api.prod.buyinggroup.com/";

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

(function setupUsabgSection() {
  const section = document.getElementById("usabgSection");
  const btn = document.getElementById("usabgLoadPos");
  const resultsEl = document.getElementById("usabgResults");

  if (!section || !btn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query(
    {
      active: true,
      currentWindow: true,
    },
    (tabs) => {
      const tab = tabs && tabs[0];
      const url = tab && tab.url ? String(tab.url) : "";
      const onUsabg =
        url.startsWith("https://app.usabuying.group/") ||
        url.startsWith("https://app.usabuying.group");

      section.style.display = onUsabg ? "block" : "none";
      if (!onUsabg) {
        resultsEl.style.display = "none";
        return;
      }

      btn.addEventListener("click", async () => {
        resultsEl.style.display = "block";
        resultsEl.textContent = "Loading buyer POs…";

        try {
          const storageData = await new Promise((resolve) => {
            chrome.storage.local.get("usabgBearerToken", resolve);
          });
          const token = storageData && storageData.usabgBearerToken;
          if (!token) {
            resultsEl.textContent =
              "No USABG token found. Visit a page that calls api.usabuying.group first.";
            return;
          }

          const response = await fetch(USABG_API_URL, {
            headers: {
              Authorization: "Bearer " + token,
            },
          });

          if (!response.ok) {
            resultsEl.textContent =
              "Request failed: " + response.status + " " + response.statusText;
            return;
          }

          const data = await response.json();
          resultsEl.textContent = JSON.stringify(data, null, 2);
        } catch (e) {
          resultsEl.textContent =
            "Error loading buyer POs. Check the console for details.";
        }
      });
    }
  );
})();

(function setupBgSection() {
  const section = document.getElementById("bgSection");
  const btn = document.getElementById("bgLoadOrders");
  const resultsEl = document.getElementById("bgResults");

  if (!section || !btn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query(
    {
      active: true,
      currentWindow: true,
    },
    (tabs) => {
      const tab = tabs && tabs[0];
      const url = tab && tab.url ? String(tab.url) : "";
      const onBg =
        url.includes("buyinggroup.com") ||
        url.startsWith("https://app.prod.buyinggroup.com") ||
        url.startsWith("https://api.prod.buyinggroup.com");

      section.style.display = onBg ? "block" : "none";
      if (!onBg) {
        resultsEl.style.display = "none";
        return;
      }

      btn.addEventListener("click", async () => {
        resultsEl.style.display = "block";
        resultsEl.textContent = "Loading orders…";

        try {
          const storageData = await new Promise((resolve) => {
            chrome.storage.local.get("bgBearerToken", resolve);
          });
          let token = storageData && storageData.bgBearerToken;
          if (!token) {
            resultsEl.textContent = "No BG token found. Trying to retrieve one…";
            token = await tryGetBgTokenFromApi();
            if (token) {
              try {
                await new Promise((resolve) =>
                  chrome.storage.local.set({ bgBearerToken: token }, resolve)
                );
              } catch {
                // ignore
              }
            }
          }
          if (!token) {
            resultsEl.textContent =
              "No BG token found. Visit the BG site and refresh, then try again.";
            return;
          }

          const formData = new FormData();
          formData.append("page", "1");
          formData.append("page_size", "50");
          formData.append("order_by", "undefined");
          formData.append("direction", "undefined");
          formData.append("date_range", "undefined");
          formData.append("user_id", "undefined");
          formData.append("serial", "undefined");
          formData.append("order_note", "undefined");
          formData.append("tracking_note", "undefined");
          formData.append("item_id", "undefined");
          formData.append("commission", "undefined");
          formData.append("price", "undefined");
          formData.append("tracking_id", "undefined");
          formData.append("location_id", "undefined");
          formData.append("time_period", "LAST_3_MONTHS");
          formData.append("no_user", "undefined");
          formData.append("enable_totalization", "false");

          const response = await fetch(BG_ORDERS_URL, {
            method: "POST",
            headers: {
              Authorization: "Bearer " + token,
            },
            body: formData,
          });

          if (!response.ok) {
            resultsEl.textContent =
              "Request failed: " + response.status + " " + response.statusText;
            return;
          }

          const data = await response.json();
          resultsEl.textContent = JSON.stringify(data, null, 2);
        } catch (e) {
          resultsEl.textContent =
            "Error loading orders. Check the console for details.";
        }
      });
    }
  );
})();

function tryParseBearerFromHeaderValue(headerValue) {
  if (!headerValue || typeof headerValue !== "string") return null;
  const match = /^Bearer\s+(.+)$/i.exec(headerValue.trim());
  return match ? match[1] : null;
}

function findLikelyTokenValue(value) {
  if (typeof value !== "string") return null;
  const s = value.trim();
  if (!s) return null;

  // JWT heuristic: three base64url-ish segments
  if (/^[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+$/.test(s)) return s;

  // Opaque token heuristic: long-ish non-whitespace
  if (s.length >= 24 && !/\s/.test(s)) return s;

  return null;
}

function findTokenInJson(obj, depth = 0) {
  if (!obj || depth > 5) return null;

  if (typeof obj === "string") return findLikelyTokenValue(obj);

  if (Array.isArray(obj)) {
    for (const v of obj) {
      const found = findTokenInJson(v, depth + 1);
      if (found) return found;
    }
    return null;
  }

  if (typeof obj === "object") {
    const preferredKeys = [
      "access_token",
      "accessToken",
      "token",
      "id_token",
      "idToken",
      "jwt",
      "bearer",
      "bearerToken",
      "bearer_token",
    ];
    for (const k of preferredKeys) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) {
        const found = findTokenInJson(obj[k], depth + 1);
        if (found) return found;
      }
    }

    for (const k of Object.keys(obj)) {
      const found = findTokenInJson(obj[k], depth + 1);
      if (found) return found;
    }
  }

  return null;
}

async function tryGetBgTokenFromApi() {
  try {
    const resp = await fetch(BG_TOKEN_URL, { credentials: "include" });

    const headerAuth =
      resp.headers.get("authorization") || resp.headers.get("Authorization");
    const headerBearer = tryParseBearerFromHeaderValue(headerAuth);
    if (headerBearer) return headerBearer;

    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const json = await resp.json();
      return findTokenInJson(json);
    }

    const text = await resp.text();
    const bearerMatch = /Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)/i.exec(
      text
    );
    if (bearerMatch && bearerMatch[1]) return bearerMatch[1];
  } catch {
    // ignore
  }
  return null;
}
