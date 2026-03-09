/** Order details from the content script (getOrderDetails). Stored after a successful fetch so "Send to Order Manager" can use it. */
let lastOrderDetails = null;

const WALMART_ORDER_DETAIL_STORAGE_KEY = "walmartOrderDetail";

function isWalmartOrderDetailUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    if (u.hostname !== "www.walmart.com") return false;
    const path = u.pathname || "";
    return path.startsWith("/orders/") || path.includes("/order-details");
  } catch {
    return false;
  }
}

/** Render saved order payload as raw JSON in #results. */
function renderOrderDetails(payload, resultsEl) {
  if (!resultsEl) return;
  const json = JSON.stringify(payload, null, 2);
  resultsEl.innerHTML =
    '<pre style="white-space:pre-wrap;margin:0;font-size:12px;">' +
    escapeHtml(json) +
    "</pre>";
}

(function setupWalmartOrderSection() {
  const section = document.getElementById("walmartOrderSection");
  const getOrderDetailsBtn = document.getElementById("getOrderDetails");
  const sendToAppBtn = document.getElementById("sendToApp");
  const resultsEl = document.getElementById("results");

  if (!section || !getOrderDetailsBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onWalmartOrderPage = isWalmartOrderDetailUrl(url);

    if (!onWalmartOrderPage) {
      section.style.display = "none";
      return;
    }

    chrome.storage.local.get(WALMART_ORDER_DETAIL_STORAGE_KEY, (storage) => {
      const stored = storage && storage[WALMART_ORDER_DETAIL_STORAGE_KEY];
      const hasStoredData =
        stored &&
        stored.payload &&
        stored.url &&
        stored.url === url;

      section.style.display = hasStoredData ? "block" : "none";
      if (!hasStoredData) return;

      getOrderDetailsBtn.addEventListener("click", () => {
        chrome.storage.local.get(WALMART_ORDER_DETAIL_STORAGE_KEY, (s) => {
          const current = s && s[WALMART_ORDER_DETAIL_STORAGE_KEY];
          if (!current || current.url !== url || !current.payload) {
            resultsEl.style.display = "block";
            resultsEl.innerHTML = '<span class="error">No saved order data for this page.</span>';
            return;
          }
          lastOrderDetails = current.payload;
          resultsEl.style.display = "block";
          renderOrderDetails(current.payload, resultsEl);
          if (sendToAppBtn) sendToAppBtn.style.display = "block";
        });
      });
    });
  });
})();

const USABG_API_URL =
  "https://api.usabuying.group/buyers/pos?limit=20&start=0";
const BG_ORDERS_URL = "https://api.prod.buyinggroup.com/v1/receipt/get_analytics";
const BG_TOKEN_URL = "https://api.prod.buyinggroup.com/";

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
