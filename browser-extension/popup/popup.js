/** Order details from the content script (getOrderDetails). Stored after a successful fetch so "Send to Order Manager" can use it. */
let lastOrderDetails = null;

const WALMART_ORDER_DETAIL_STORAGE_KEY = "walmartOrderDetail";
const COSTCO_ORDER_DETAIL_STORAGE_KEY = "costcoOrderDetailsGraphqlCapture";
const WALMART_BULK_JOB_STORAGE_KEY = "walmartBulkJob";
const COSTCO_BULK_JOB_STORAGE_KEY = "costcoBulkJob";
const AMAZON_BULK_JOB_STORAGE_KEY = "amazonBulkJob";
const AMAZON_ORDER_DETAIL_STORAGE_KEY = "amazonOrderDetail";
const AMAZON_ACCOUNT_EMAIL_STORAGE_KEY = "amazonAccountEmail";
const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY = "orderManagerApiBaseUrl";
const ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY = "orderManagerProdApiBaseUrl";
const ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY = "orderManagerDevApiBaseUrl";
const ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY = "orderManagerDevEnabled";
const ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY = "orderManagerActiveServer"; // "prod" | "dev"

const ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY = "orderManagerExtensionToken";
const ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY = "orderManagerExtensionTokenProd";
const ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY = "orderManagerExtensionTokenDev";

function normalizeActiveServer(v) {
  return v === "dev" ? "dev" : "prod";
}

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

function isWalmartOrdersListUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    if (u.hostname !== "www.walmart.com") return false;
    const path = u.pathname || "";
    return path === "/orders";
  } catch {
    return false;
  }
}

function isCostcoOrdersAndPurchasesUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    if (u.hostname !== "www.costco.com") return false;
    if (!u.pathname.startsWith("/myaccount")) return false;
    return /#\/app\/[^/]+\/ordersandpurchases/.test(u.hash || "");
  } catch {
    return false;
  }
}

function isCostcoOrderDetailsUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    if (u.hostname !== "www.costco.com") return false;
    if (!u.pathname.startsWith("/myaccount")) return false;
    return /#\/app\/[^/]+\/orderdetails/.test(u.hash || "");
  } catch {
    return false;
  }
}

function isAmazonOrdersListUrl(url) {
  if (globalThis.OrderManagerAmazon && typeof globalThis.OrderManagerAmazon.isAmazonOrdersListUrl === "function") {
    return globalThis.OrderManagerAmazon.isAmazonOrdersListUrl(url);
  }
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    const host = (u.hostname || "").toLowerCase();
    if (host !== "www.amazon.com" && host !== "amazon.com" && !host.endsWith(".amazon.com")) {
      return false;
    }
    const path = u.pathname || "";
    const hash = u.hash || "";
    if (path.includes("/your-orders") || path.includes("order-history")) return true;
    if (/^#time\//i.test(hash) || hash.includes("/pagination/")) return true;
    return false;
  } catch {
    return false;
  }
}

function isAmazonOrderDetailUrl(url) {
  if (globalThis.OrderManagerAmazon && typeof globalThis.OrderManagerAmazon.isAmazonOrderDetailUrl === "function") {
    return globalThis.OrderManagerAmazon.isAmazonOrderDetailUrl(url);
  }
  if (!url || typeof url !== "string") return false;
  try {
    const u = new URL(url);
    if (u.hostname !== "www.amazon.com" && u.hostname !== "amazon.com") return false;
    return /order-details|orderI[Dd]=/.test(u.href || "");
  } catch {
    return false;
  }
}

function isAmazonPageUrl(url) {
  if (globalThis.OrderManagerAmazon && typeof globalThis.OrderManagerAmazon.isAmazonPageUrl === "function") {
    return globalThis.OrderManagerAmazon.isAmazonPageUrl(url);
  }
  if (!url || typeof url !== "string") return false;
  try {
    const h = new URL(url).hostname.toLowerCase();
    return h === "www.amazon.com" || h === "amazon.com" || h.endsWith(".amazon.com");
  } catch {
    return false;
  }
}

function withAmazonDisableCsdUrl(url) {
  if (globalThis.OrderManagerAmazon && typeof globalThis.OrderManagerAmazon.withDisableCsdParam === "function") {
    return globalThis.OrderManagerAmazon.withDisableCsdParam(url);
  }
  try {
    const u = new URL(String(url));
    if (!u.searchParams.has("disableCsd")) {
      u.searchParams.set("disableCsd", "missing-library");
    }
    return u.toString();
  } catch {
    return url;
  }
}

function waitForAmazonTabReady(tabId, callback, timeoutMs) {
  const deadline = Date.now() + (typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 45000);
  let done = false;

  function finish() {
    if (done) return;
    done = true;
    try {
      chrome.tabs.onUpdated.removeListener(onUpdated);
    } catch {
      // ignore
    }
    setTimeout(callback, 1500);
  }

  function onUpdated(id, info) {
    if (id !== tabId || info.status !== "complete") return;
    finish();
  }

  try {
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        finish();
        return;
      }
      if (tab && tab.status === "complete") finish();
    });
  } catch {
    finish();
    return;
  }

  setTimeout(finish, Math.max(0, deadline - Date.now()));
}

function getOrderManagerActiveServer(callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    callback("prod");
    return;
  }
  try {
    chrome.storage.local.get(ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback("prod");
        return;
      }
      const value =
        data && data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]).trim()
          : "prod";
      callback(normalizeActiveServer(value));
    });
  } catch {
    callback("prod");
  }
}

function setOrderManagerActiveServer(server, callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    if (callback) callback(false);
    return;
  }
  const normalized = normalizeActiveServer(server);
  try {
    chrome.storage.local.set(
      { [ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]: normalized },
      () => {
        if (callback) callback(!(chrome.runtime && chrome.runtime.lastError));
      }
    );
  } catch {
    if (callback) callback(false);
  }
}

function getOrderManagerApiBaseUrlForServer(server, callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    callback(null);
    return;
  }
  try {
    const normalizedServer = normalizeActiveServer(server);
    const keys =
      normalizedServer === "dev"
        ? [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY, ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
        : [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY, ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY];

    chrome.storage.local.get(keys, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback(null);
        return;
      }

      if (normalizedServer === "dev") {
        const enabled = !!(data && data[ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]);
        const value =
          data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
            ? String(data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]).trim()
            : "";
        callback(enabled && value ? value : null);
        return;
      }

      const value =
        data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]).trim()
          : data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]
            ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]).trim()
            : "";
      callback(value || null);
    });
  } catch {
    callback(null);
  }
}

function getOrderManagerApiBaseUrl(callback) {
  if (globalThis.OrderManagerWalmart && typeof globalThis.OrderManagerWalmart.getOrderManagerApiBaseUrl === "function") {
    globalThis.OrderManagerWalmart.getOrderManagerApiBaseUrl(callback);
    return;
  }
  getOrderManagerActiveServer((server) => {
    getOrderManagerApiBaseUrlForServer(server, callback);
  });
}

function getOrderManagerAuthTokenForServer(server, callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    callback(null);
    return;
  }
  try {
    const normalizedServer = normalizeActiveServer(server);
    const key =
      normalizedServer === "dev"
        ? ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY
        : ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY;

    chrome.storage.local.get([key, ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY], (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback(null);
        return;
      }
      const value =
        data && data[key]
          ? String(data[key]).trim()
          : normalizedServer === "prod" &&
              data &&
              data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY]
            ? String(data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY]).trim()
          : "";
      callback(value || null);
    });
  } catch {
    callback(null);
  }
}

function getOrderManagerAuthToken(callback) {
  getOrderManagerActiveServer((server) => {
    getOrderManagerAuthTokenForServer(server, callback);
  });
}

function openExtensionOptionsPage() {
  try {
    if (chrome && chrome.runtime && typeof chrome.runtime.openOptionsPage === "function") {
      chrome.runtime.openOptionsPage();
      return true;
    }
  } catch {
    // ignore
  }
  return false;
}

(function setupSettingsButton() {
  const btn = document.getElementById("openSettingsGlobal");
  if (!btn) return;
  btn.addEventListener("click", () => {
    openExtensionOptionsPage();
  });
})();

(function setupDevServerToggle() {
  const toggle = document.getElementById("devServerToggle");
  if (!toggle) return;

  // Default to off if not set.
  getOrderManagerActiveServer((server) => {
    toggle.checked = server === "dev";
  });

  toggle.addEventListener("change", () => {
    const next = toggle.checked ? "dev" : "prod";
    setOrderManagerActiveServer(next, () => {
      // Re-render auth section state based on new active server
      try {
        window.dispatchEvent(new CustomEvent("orderManagerActiveServerChanged"));
      } catch {
        // ignore
      }
    });
  });
})();

(function setupOrderManagerAuthSection() {
  const section = document.getElementById("orderManagerSection");
  const connectBtn = document.getElementById("orderManagerConnect");
  const disconnectBtn = document.getElementById("orderManagerDisconnect");
  const statusEl = document.getElementById("orderManagerStatus");

  if (!section || !connectBtn || !disconnectBtn || !statusEl || !chrome.tabs) return;

  function showLoggedIn() {
    connectBtn.style.display = "none";
    disconnectBtn.style.display = "";
    statusEl.textContent = "";
  }

  function showLoggedOut() {
    connectBtn.style.display = "";
    disconnectBtn.style.display = "none";
    statusEl.textContent = "";
  }

  function refreshAuthUi() {
    try {
      getOrderManagerAuthToken((token) => {
        if (token) {
          showLoggedIn();
        } else {
          showLoggedOut();
        }
      });
    } catch {
      // ignore
    }
  }

  refreshAuthUi();

  window.addEventListener("orderManagerActiveServerChanged", () => {
    refreshAuthUi();
  });

  disconnectBtn.addEventListener("click", () => {
    try {
      getOrderManagerActiveServer((server) => {
        const keysToRemove =
          server === "dev"
            ? [ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY]
            : [ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY, ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY];
        chrome.storage.local.remove(keysToRemove, () => {
          showLoggedOut();
        });
      });
    } catch {
      showLoggedOut();
    }
  });

  connectBtn.addEventListener("click", () => {
    statusEl.textContent = "Opening authorization…";
    getOrderManagerActiveServer((server) => {
      getOrderManagerApiBaseUrlForServer(server, (baseUrl) => {
      if (!baseUrl) {
        statusEl.innerHTML =
          'Order Manager base URL is not configured for the selected server. Use <span style="font-weight:600;">Settings</span> to set it first.';
        return;
      }
      try {
        let authUrl = baseUrl;
        if (authUrl.endsWith("/")) {
          authUrl = authUrl.slice(0, -1);
        }
        authUrl += "/extension-auth";

        chrome.windows.create({ url: authUrl, type: "popup", width: 500, height: 620 }, () => {
          statusEl.textContent =
            "Sign in and authorize in the popup window.";
          const poll = setInterval(() => {
            getOrderManagerAuthTokenForServer(server, (token) => {
              if (token) {
                clearInterval(poll);
                showLoggedIn();
              }
            });
          }, 2000);
          setTimeout(() => clearInterval(poll), 60000);
        });
      } catch {
        statusEl.textContent =
          "Could not open Order Management authorization window.";
      }
    });
    });
  });
})();

function normalizeWalmartOrderDetailPayload(payload, sourceUrl) {
  if (globalThis.OrderManagerWalmart && typeof globalThis.OrderManagerWalmart.normalizeWalmartOrderDetailPayload === "function") {
    return globalThis.OrderManagerWalmart.normalizeWalmartOrderDetailPayload(payload, sourceUrl);
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("Missing Walmart order payload.");
  }

  const raw = payload.raw || null;
  let order = payload.order || null;

  if (!order && raw && raw.props && raw.props.pageProps) {
    try {
      const pageProps = raw.props.pageProps;
      if (
        pageProps.initialData &&
        pageProps.initialData.data &&
        pageProps.initialData.data.order
      ) {
        order = pageProps.initialData.data.order;
      }
    } catch {
      // ignore, will validate below
    }
  }

  if (!order || !order.id) {
    throw new Error("Walmart order structure not recognized.");
  }

  const customer = order.customer || {};
  const groups = Array.isArray(order.groups_2101) ? order.groups_2101 : [];
  let overallStatusType = null;
  try {
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i] || {};
      const status = g.status || {};
      const statusType =
        typeof status.statusType === "string" ? status.statusType.trim() : "";
      if (!statusType) continue;
      const lower = statusType.toLowerCase();
      if (overallStatusType == null) overallStatusType = statusType;
      if (lower.includes("canceled") || lower.includes("cancelled")) {
        overallStatusType = statusType;
        break;
      }
    }
  } catch {
    // ignore
  }
  const paymentMethodsRaw = Array.isArray(order.paymentMethods)
    ? order.paymentMethods
    : [];

  const shipments = [];
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i] || {};
    const status = g.status || {};
    const shipment = g.shipment || {};
    const subtotal = g.subtotal || {};

    const shipmentEntry = {
      shipmentId: shipment.id || null,
      groupId: g.id || null,
      trackingNumber: shipment.trackingNumber || null,
      trackingUrl: shipment.trackingUrl || null,
      purchaseOrderId: shipment.purchaseOrderId || null,
      deliveryDate: g.deliveryDate || null,
      fulfillmentType: g.fulfillmentType || null,
      detailedGroupType: g.detailedGroupType || null,
      status: {
        rawStatusType: status.statusType || null,
        normalizedStatus: status.statusType || null,
        message:
          status.message &&
          Array.isArray(status.message.parts) &&
          status.message.parts.length > 0 &&
          status.message.parts[0] &&
          typeof status.message.parts[0].text === "string"
            ? status.message.parts[0].text
            : null,
      },
      financials: {
        subtotal:
          typeof subtotal.value === "number" ? subtotal.value : null,
      },
    };

    shipments.push(shipmentEntry);
  }

  const shippingGroup = groups[0] || {};
  const deliveryAddress = shippingGroup.deliveryAddress || {};
  const deliveryAddressAddress = deliveryAddress.address || {};

  const shippingAddress = {
    fullName: deliveryAddress.fullName || null,
    addressLine1: deliveryAddressAddress.addressLineOne || null,
    addressLine2: deliveryAddressAddress.addressLineTwo || null,
    city: deliveryAddressAddress.city || null,
    state: deliveryAddressAddress.state || null,
    postalCode: deliveryAddressAddress.postalCode || null,
    country: deliveryAddressAddress.country || null,
    phoneNumber: deliveryAddressAddress.phoneNumber || null,
  };

  const items = [];
  for (let gi = 0; gi < groups.length; gi++) {
    const g = groups[gi] || {};
    const shipment = g.shipment || {};
    const shipmentId = shipment.id || null;
    // Prefer detailed items from categories (have itemPrice/unitPrice); fall back to group.items
    let groupItems = [];
    if (Array.isArray(g.categories) && g.categories.length > 0) {
      for (let ci = 0; ci < g.categories.length; ci++) {
        const catItems = g.categories[ci] && Array.isArray(g.categories[ci].items) ? g.categories[ci].items : [];
        groupItems = groupItems.concat(catItems);
      }
    }
    if (groupItems.length === 0 && Array.isArray(g.items)) {
      groupItems = g.items;
    }

    for (let ii = 0; ii < groupItems.length; ii++) {
      const item = groupItems[ii] || {};
      const productInfo = item.productInfo || {};
      const priceInfo = item.priceInfo || {};
      const itemPrice = priceInfo.itemPrice || {};
      const unitPriceObj = priceInfo.unitPrice || {};
      const linePrice = priceInfo.linePrice || {};
      const strikethroughPrice = priceInfo.strikethroughPrice || {};
      const qty = typeof item.quantity === "number" ? item.quantity : 1;
      const linePriceVal = typeof linePrice.value === "number" ? linePrice.value : null;
      // Unit price: itemPrice (per-ea), unitPrice, or derived from linePrice / quantity
      let unitPriceVal =
        typeof itemPrice.value === "number"
          ? itemPrice.value
          : typeof unitPriceObj.value === "number"
            ? unitPriceObj.value
            : null;
      if (unitPriceVal == null && linePriceVal != null && qty > 0) {
        unitPriceVal = linePriceVal / qty;
      }
      const lineTotal =
        linePriceVal != null
          ? linePriceVal
          : unitPriceVal != null
            ? unitPriceVal * qty
            : null;

      const variants = Array.isArray(item.selectedVariants)
        ? item.selectedVariants.map(function (v) {
            return {
              name: v && v.name ? String(v.name) : null,
              value: v && v.value ? String(v.value) : null,
            };
          })
        : [];

      items.push({
        logicalItemId:
          item.id != null ? String(item.id) : item.usItemId || null,
        externalSku: productInfo.usItemId || null,
        externalOfferId: productInfo.offerId || null,
        name: productInfo.name || null,
        productUrl: productInfo.canonicalUrl || null,
        imageUrl:
          productInfo.imageInfo && productInfo.imageInfo.thumbnailUrl
            ? productInfo.imageInfo.thumbnailUrl
            : null,
        variants: variants,
        quantities: {
          ordered:
            typeof item.quantity === "number" ? item.quantity : null,
        },
        pricing: {
          unitPrice: unitPriceVal,
          linePrice: linePriceVal,
          lineTotal: typeof lineTotal === "number" ? lineTotal : null,
          strikethroughPrice:
            typeof strikethroughPrice.value === "number"
              ? strikethroughPrice.value
              : null,
          discounts: [], // can be extended later from item.discounts
        },
        status: {
          rawStatusCode: item.statusCode || null,
          normalizedStatus: null,
        },
        shipments:
          shipmentId && item.quantity
            ? [
                {
                  shipmentId: shipmentId,
                  quantity: item.quantity,
                  normalizedStatus: null,
                },
              ]
            : [],
        returnability: {
          isReturnable: !!item.isReturnable,
          returnEligibilityMessage:
            item.returnEligibilityMessage || null,
        },
      });
    }
  }

  const itemCancelReasons = Array.isArray(order.itemCancelReasons)
    ? order.itemCancelReasons.map(function (r) {
        return {
          code:
            r && r.subReasonCode != null
              ? String(r.subReasonCode)
              : null,
          description:
            r && typeof r.subDescription === "string"
              ? r.subDescription
              : null,
        };
      })
    : [];

  const totals = {
    itemCount:
      typeof order.itemCount === "number" ? order.itemCount : null,
    subtotal:
      shippingGroup.subtotal &&
      typeof shippingGroup.subtotal.value === "number"
        ? shippingGroup.subtotal.value
        : null,
    grandTotal:
      order.priceDetails &&
      order.priceDetails.grandTotal &&
      typeof order.priceDetails.grandTotal.value === "number"
        ? order.priceDetails.grandTotal.value
        : null,
  };

  const externalUrl =
    sourceUrl || payload.url || (typeof document !== "undefined"
      ? document.location && document.location.href
      : null);

  return {
    store: "walmart",
    source: "browser-extension",
    capturedAt: new Date().toISOString(),
    externalOrder: {
      id: String(order.id),
      orderDate: order.orderDate || null,
      timezone: order.timezone || null,
      url: externalUrl || null,
      statusType: overallStatusType || null,
    },
    customer: {
      email: customer.email || null,
      firstName: customer.firstName || null,
      lastName: customer.lastName || null,
    },
    shippingAddress: shippingAddress,
    shipments: shipments,
    paymentMethods: paymentMethodsRaw.map(function (pm) {
      const description =
        typeof pm.description === "string" ? pm.description : null;
      let last4 = null;
      if (description) {
        const m = /(\d{4})\b/.exec(description);
        if (m) last4 = m[1];
      }
      return {
        description: description,
        cardType:
          typeof pm.cardType === "string" ? pm.cardType : null,
        paymentType:
          typeof pm.paymentType === "string" ? pm.paymentType : null,
        last4: last4,
      };
    }),
    items: items,
    cancellations: {
      orderLevel: Array.isArray(order.cancelReasons)
        ? order.cancelReasons
        : [],
      itemLevelReasons: itemCancelReasons,
    },
    totals: totals,
  };
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

      if (sendToAppBtn) {
        sendToAppBtn.addEventListener("click", () => {
          resultsEl.style.display = "block";
          resultsEl.textContent = "Opening Order Manager…";

          chrome.storage.local.get(
            WALMART_ORDER_DETAIL_STORAGE_KEY,
            (s) => {
              const current = s && s[WALMART_ORDER_DETAIL_STORAGE_KEY];
              if (!current || current.url !== url || !current.payload) {
                resultsEl.innerHTML =
                  '<span class="error">No saved order data for this page.</span>';
                return;
              }

              getOrderManagerApiBaseUrl((baseUrl) => {
                if (!baseUrl) {
                  resultsEl.innerHTML =
                    '<div class="error">Order Manager base URL is not configured.</div>' +
                    '<div style="margin-top: 8px;">' +
                    '  <button id="openSettings" style="width: auto; padding: 8px 10px; font-size: 13px;">Open settings</button>' +
                    "</div>";
                  const btn = document.getElementById("openSettings");
                  if (btn) {
                    btn.addEventListener("click", () => {
                      const ok = openExtensionOptionsPage();
                      if (!ok) {
                        resultsEl.innerHTML =
                          '<span class="error">Could not open settings. Please open the extension details and choose “Extension options”.</span>';
                      }
                    });
                  }
                  return;
                }

                let body;
                try {
                  body = normalizeWalmartOrderDetailPayload(
                    current.payload,
                    current.url
                  );
                } catch (e) {
                  resultsEl.innerHTML =
                    '<span class="error">Could not normalize Walmart order data: ' +
                    escapeHtml(String(e && e.message ? e.message : e)) +
                    "</span>";
                  return;
                }

                try {
                  openBulkReviewForOrders([body], resultsEl);
                } catch (e) {
                  resultsEl.innerHTML =
                    '<span class="error">Could not start bulk import review: ' +
                    escapeHtml(String(e && e.message ? e.message : e)) +
                    "</span>";
                }
              });
            }
          );
        });
      }
    });
  });
})();

(function setupAmazonUnsupportedSection() {
  const section = document.getElementById("amazonUnsupportedSection");
  if (!section || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const url = tabs && tabs[0] && tabs[0].url ? String(tabs[0].url) : "";
    const onAmazon = isAmazonPageUrl(url);
    const onList = isAmazonOrdersListUrl(url);
    const onDetail = isAmazonOrderDetailUrl(url);
    section.style.display = onAmazon && !onList && !onDetail ? "block" : "none";
  });
})();

(function setupAmazonBulkSection() {
  const section = document.getElementById("amazonBulkSection");
  const pagesInput = document.getElementById("amazonBulkPages");
  const startBtn = document.getElementById("amazonStartBulk");
  const resultsEl = document.getElementById("amazonBulkResults");

  if (!section || !pagesInput || !startBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onAmazonOrdersList = isAmazonOrdersListUrl(url);

    section.style.display = onAmazonOrdersList ? "block" : "none";
    if (!onAmazonOrdersList || !tab || typeof tab.id !== "number") {
      resultsEl.style.display = "none";
      return;
    }

    startBtn.addEventListener("click", () => {
      let pages = parseInt(String(pagesInput.value || "1"), 10);
      if (!Number.isFinite(pages) || pages <= 0) pages = 1;
      if (pages > 50) pages = 50;
      pagesInput.value = String(pages);

      resultsEl.style.display = "block";
      resultsEl.innerHTML = '<div class="loading">Starting…</div>';

      chrome.storage.local.set(
        {
          [AMAZON_BULK_JOB_STORAGE_KEY]: {
            store: "amazon",
            createdAt: Date.now(),
            sourceTabId: tab.id,
            maxPages: pages,
          },
        },
        () => {
          try {
            const bulkUrl = chrome.runtime.getURL("bulk/amazon-bulk.html");
            chrome.tabs.create({ url: bulkUrl, active: true });
            try {
              window.close();
            } catch {
              // ignore
            }
          } catch (e) {
            resultsEl.innerHTML =
              '<span class="error">Could not open bulk import tab: ' +
              escapeHtml(String(e && e.message ? e.message : e)) +
              "</span>";
          }
        }
      );
    });
  });
})();

(function setupAmazonOrderDetailSection() {
  const section = document.getElementById("amazonOrderDetailSection");
  const importBtn = document.getElementById("amazonImportThisOrder");
  const resultsEl = document.getElementById("amazonOrderDetailResults");

  if (!section || !importBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onDetail = isAmazonOrderDetailUrl(url);

    section.style.display = onDetail ? "block" : "none";
    if (!onDetail) return;

    importBtn.addEventListener("click", () => {
      resultsEl.style.display = "block";
      resultsEl.innerHTML = '<div class="loading">Reading order…</div>';

      function finishImport(captured, pageUrl) {
        if (!captured || !captured.payload) {
          resultsEl.innerHTML =
            '<span class="error">No order data captured yet. Wait for the page to finish loading, or refresh and try again.</span>';
          return;
        }

        chrome.storage.local.get(AMAZON_ACCOUNT_EMAIL_STORAGE_KEY, (emailData) => {
          const emailRow =
            emailData && emailData[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY]
              ? emailData[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY]
              : null;
          const accountEmail = emailRow && emailRow.email ? String(emailRow.email) : null;

          getOrderManagerApiBaseUrl((baseUrl) => {
            if (!baseUrl) {
              resultsEl.innerHTML =
                '<div class="error">Order Manager base URL is not configured.</div>';
              return;
            }

            let body;
            try {
              if (
                !globalThis.OrderManagerAmazon ||
                typeof globalThis.OrderManagerAmazon.normalizeAmazonOrderPayload !== "function"
              ) {
                throw new Error("Amazon normalizer is not available.");
              }
              body = globalThis.OrderManagerAmazon.normalizeAmazonOrderPayload(
                captured.payload,
                captured.url || pageUrl || url,
                accountEmail
              );
            } catch (e) {
              resultsEl.innerHTML =
                '<span class="error">Could not normalize Amazon order: ' +
                escapeHtml(String(e && e.message ? e.message : e)) +
                "</span>";
              return;
            }

            openBulkReviewForOrders([body], resultsEl);
          });
        });
      }

      function requestParsedOrder(pageUrl) {
        if (tab && typeof tab.id === "number") {
          chrome.tabs.sendMessage(
            tab.id,
            { store: "amazon", type: "amazonParseCurrentDetailPage" },
            (resp) => {
              if (resp && resp.success === true && resp.order) {
                finishImport({ url: pageUrl, payload: resp.order }, pageUrl);
                return;
              }
              chrome.storage.local.get(AMAZON_ORDER_DETAIL_STORAGE_KEY, (s) => {
                const captured = s && s[AMAZON_ORDER_DETAIL_STORAGE_KEY];
                finishImport(captured, pageUrl);
              });
            }
          );
        } else {
          chrome.storage.local.get(AMAZON_ORDER_DETAIL_STORAGE_KEY, (s) => {
            finishImport(s && s[AMAZON_ORDER_DETAIL_STORAGE_KEY], pageUrl);
          });
        }
      }

      const detailUrl = withAmazonDisableCsdUrl(url);
      if (detailUrl !== url && tab && typeof tab.id === "number") {
        resultsEl.innerHTML = '<div class="loading">Loading full order details…</div>';
        chrome.tabs.update(tab.id, { url: detailUrl }, () => {
          waitForAmazonTabReady(tab.id, () => requestParsedOrder(detailUrl));
        });
        return;
      }

      requestParsedOrder(url);
    });
  });
})();

(function setupWalmartBulkSection() {
  const section = document.getElementById("walmartBulkSection");
  const pagesInput = document.getElementById("walmartBulkPages");
  const startBtn = document.getElementById("walmartStartBulk");
  const resultsEl = document.getElementById("walmartBulkResults");

  if (!section || !pagesInput || !startBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onWalmartOrdersList = isWalmartOrdersListUrl(url);

    section.style.display = onWalmartOrdersList ? "block" : "none";
    if (!onWalmartOrdersList || !tab || typeof tab.id !== "number") {
      resultsEl.style.display = "none";
      return;
    }

    function appendStatusRow(text, status) {
      if (!resultsEl) return;
      const row = document.createElement("div");
      row.textContent = text;
      if (status === "error") {
        row.style.color = "#c00";
      } else if (status === "ok") {
        row.style.color = "#065f46";
      } else if (status === "pending") {
        row.style.color = "#555";
      }
      resultsEl.appendChild(row);
    }

    async function startBulkInExtensionTab(orderNumbers, maxPages) {
      resultsEl.style.display = "block";
      resultsEl.innerHTML = "";
      appendStatusRow("Opening bulk import tab…", "pending");

      try {
        await new Promise((resolve) => {
          chrome.storage.local.set(
            {
              [WALMART_BULK_JOB_STORAGE_KEY]: {
                store: "walmart",
                createdAt: Date.now(),
                sourceTabId: tab.id,
                maxPages: typeof maxPages === "number" ? maxPages : null,
                orderNumbers: Array.isArray(orderNumbers) ? orderNumbers : [],
              },
            },
            () => resolve(null)
          );
        });
      } catch (e) {
        appendStatusRow(
          "Could not start bulk import (" + String(e && e.message ? e.message : e) + ")",
          "error"
        );
        return;
      }

      try {
        const url = chrome.runtime.getURL("bulk/walmart-bulk.html");
        chrome.tabs.create({ url, active: true });
        try {
          window.close();
        } catch {
          // ignore (best-effort; some browsers may block programmatic close)
        }
        appendStatusRow("Started. Continue in the bulk import tab.", "ok");
      } catch (e) {
        appendStatusRow(
          "Could not open bulk import tab (" + String(e && e.message ? e.message : e) + ")",
          "error"
        );
      }
    }

    startBtn.addEventListener("click", () => {
      let raw = pagesInput.value;
      let pages = parseInt(String(raw), 10);
      if (!Number.isFinite(pages) || pages <= 0) pages = 1;
      if (pages > 50) pages = 50;
      pagesInput.value = String(pages);

      resultsEl.style.display = "block";
      resultsEl.innerHTML = '<div class="loading">Starting…</div>';

      // Open the extension progress tab first, and let background collect order numbers
      // (popup closes when the tab opens, so we can't own async callbacks here).
      chrome.storage.local.set(
        {
          [WALMART_BULK_JOB_STORAGE_KEY]: {
            store: "walmart",
            createdAt: Date.now(),
            sourceTabId: tab.id,
            maxPages: pages,
            orderNumbers: [],
          },
        },
        () => {
          startBulkInExtensionTab([], pages);
          appendStatusRow("Started. Continue in the bulk import tab.", "ok");
        }
      );
    });
  });
})();

(function setupCostcoBulkSection() {
  const section = document.getElementById("costcoBulkSection");
  const startBtn = document.getElementById("costcoStartBulk");
  const resultsEl = document.getElementById("costcoBulkResults");

  if (!section || !startBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onCostcoOrders = isCostcoOrdersAndPurchasesUrl(url);

    section.style.display = onCostcoOrders ? "block" : "none";
    if (!onCostcoOrders || !tab || typeof tab.id !== "number") {
      resultsEl.style.display = "none";
      return;
    }

    function appendStatusRow(text, status) {
      if (!resultsEl) return;
      const row = document.createElement("div");
      row.textContent = text;
      if (status === "error") {
        row.style.color = "#c00";
      } else if (status === "ok") {
        row.style.color = "#065f46";
      } else if (status === "pending") {
        row.style.color = "#555";
      }
      resultsEl.appendChild(row);
    }

    async function startBulkInExtensionTab() {
      resultsEl.style.display = "block";
      resultsEl.innerHTML = "";
      appendStatusRow("Opening bulk import tab…", "pending");

      try {
        await new Promise((resolve) => {
          chrome.storage.local.set(
            {
              [COSTCO_BULK_JOB_STORAGE_KEY]: {
                store: "costco",
                createdAt: Date.now(),
                sourceTabId: tab.id,
              },
            },
            () => resolve(null)
          );
        });
      } catch (e) {
        appendStatusRow(
          "Could not start bulk import (" + String(e && e.message ? e.message : e) + ")",
          "error"
        );
        return;
      }

      try {
        const url = chrome.runtime.getURL("bulk/costco-bulk.html");
        chrome.tabs.create({ url, active: true });
        try {
          window.close();
        } catch {
          // ignore
        }
        appendStatusRow("Started. Continue in the bulk import tab.", "ok");
      } catch (e) {
        appendStatusRow(
          "Could not open bulk import tab (" + String(e && e.message ? e.message : e) + ")",
          "error"
        );
      }
    }

    startBtn.addEventListener("click", () => {
      resultsEl.style.display = "block";
      resultsEl.innerHTML = '<div class="loading">Starting…</div>';
      startBulkInExtensionTab();
    });
  });
})();

(function setupCostcoSingleOrderDetailSection() {
  const section = document.getElementById("costcoOrderDetailSection");
  const importBtn = document.getElementById("costcoImportThisOrder");
  const resultsEl = document.getElementById("costcoOrderDetailResults");

  if (!section || !importBtn || !resultsEl || !chrome.tabs) return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    const url = tab && tab.url ? String(tab.url) : "";
    const onCostcoOrderDetail = isCostcoOrderDetailsUrl(url);

    section.style.display = onCostcoOrderDetail ? "block" : "none";
    if (!onCostcoOrderDetail) return;

    function appendRow(text, status) {
      const row = document.createElement("div");
      row.textContent = text;
      if (status === "error") row.style.color = "#c00";
      else if (status === "ok") row.style.color = "#065f46";
      else row.style.color = "#555";
      resultsEl.appendChild(row);
    }

    importBtn.addEventListener("click", () => {
      resultsEl.style.display = "block";
      resultsEl.innerHTML = "";
      appendRow("Reading captured GraphQL order details…", "pending");

      chrome.storage.local.get(COSTCO_ORDER_DETAIL_STORAGE_KEY, (s) => {
        const captured = s && s[COSTCO_ORDER_DETAIL_STORAGE_KEY] ? s[COSTCO_ORDER_DETAIL_STORAGE_KEY] : null;
        if (!captured || !captured.payload) {
          resultsEl.innerHTML =
            '<span class="error">No saved Costco order details captured yet. Refresh this order detail page and try again.</span>';
          return;
        }

        appendRow("Preparing import review…", "pending");

        getOrderManagerApiBaseUrl((baseUrl) => {
          if (!baseUrl) {
            resultsEl.innerHTML =
              '<div class="error">Order Manager base URL is not configured.</div>' +
              '<div style="margin-top: 8px;">' +
              '  <button id="openSettingsCostcoSingle" style="width: auto; padding: 8px 10px; font-size: 13px;">Open settings</button>' +
              "</div>";
            const btn = document.getElementById("openSettingsCostcoSingle");
            if (btn) {
              btn.addEventListener("click", () => {
                const ok = openExtensionOptionsPage();
                if (!ok) {
                  resultsEl.innerHTML =
                    '<span class="error">Could not open settings. Please open the extension details and choose “Extension options”.</span>';
                }
              });
            }
            return;
          }

          let body;
          try {
            if (
              !globalThis.OrderManagerCostco ||
              typeof globalThis.OrderManagerCostco.normalizeCostcoOrderDetailsGraphqlPayload !== "function"
            ) {
              throw new Error("Costco normalizer is not available.");
            }
            body = globalThis.OrderManagerCostco.normalizeCostcoOrderDetailsGraphqlPayload(
              captured.payload,
              url
            );
          } catch (e) {
            resultsEl.innerHTML =
              '<span class="error">Could not normalize Costco order detail data: ' +
              escapeHtml(String(e && e.message ? e.message : e)) +
              "</span>";
            return;
          }

          try {
            openBulkReviewForOrders([body], resultsEl);
          } catch (e) {
            resultsEl.innerHTML =
              '<span class="error">Could not start bulk import review: ' +
              escapeHtml(String(e && e.message ? e.message : e)) +
              "</span>";
          }
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

function openBulkReviewForOrders(orders, resultsEl) {
  if (!chrome || !chrome.runtime || typeof chrome.runtime.sendMessage !== "function") {
    if (resultsEl) {
      resultsEl.innerHTML =
        '<span class="error">Extension messaging is not available in this browser context.</span>';
    }
    return;
  }

  chrome.runtime.sendMessage({ type: "openBulkReview", orders }, (resp) => {
    if (chrome.runtime && chrome.runtime.lastError) {
      if (resultsEl) {
        resultsEl.innerHTML =
          '<span class="error">Could not start import review: ' +
          escapeHtml(String(chrome.runtime.lastError.message || "Unknown error")) +
          "</span>";
      }
      return;
    }

    if (!resp || resp.success !== true) {
      if (resultsEl) {
        resultsEl.innerHTML =
          '<span class="error">Could not start import review: ' +
          escapeHtml(String(resp && resp.error ? resp.error : "Unknown error")) +
          "</span>";
      }
      return;
    }

    try {
      window.close();
    } catch {
      // ignore
    }

    if (resultsEl) {
      resultsEl.innerHTML = "<span>Opened bulk import review in Order Manager.</span>";
    }
  });
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
