const ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY = "orderManagerExtensionToken";
const ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY = "orderManagerExtensionTokenProd";
const ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY = "orderManagerExtensionTokenDev";

const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY = "orderManagerApiBaseUrl";
const ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY = "orderManagerProdApiBaseUrl";
const ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY = "orderManagerDevApiBaseUrl";
const EXT_TOKEN_HASH_PREFIX = "#ext-token=";

// Shared helpers (classic worker-compatible)
try {
  // eslint-disable-next-line no-undef
  importScripts("lib/walmart.js");
} catch {
  // ignore (Firefox may not support importScripts in some contexts)
}

function normalizeBaseUrl(s) {
  const v = String(s || "").trim();
  if (!v) return "";
  try {
    const url = new URL(v);
    let normalized = url.toString();
    if (normalized.endsWith("/")) normalized = normalized.slice(0, -1);
    return normalized;
  } catch {
    return "";
  }
}

function baseUrlToOrigin(baseUrl) {
  const normalized = normalizeBaseUrl(baseUrl);
  if (!normalized) return "";
  try {
    return new URL(normalized).origin;
  } catch {
    return "";
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  try {
    const url = new URL(changeInfo.url);
    if (!url.hash || !url.hash.startsWith(EXT_TOKEN_HASH_PREFIX)) return;
    if (!url.pathname.includes("/extension-auth")) return;

    const token = decodeURIComponent(url.hash.slice(EXT_TOKEN_HASH_PREFIX.length));
    if (!token) return;

    chrome.storage.local.get(
      [
        ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY,
        ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY,
        ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY,
      ],
      (data) => {
        const prodBase =
          (data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]) ||
          (data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]) ||
          "";
        const devBase = (data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]) || "";

        const origin = url.origin;
        const prodOrigin = baseUrlToOrigin(prodBase);
        const devOrigin = baseUrlToOrigin(devBase);

        const targetKey =
          devOrigin && origin === devOrigin
            ? ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY
            : ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY;

        chrome.storage.local.set(
          {
            [targetKey]: token,
            ...(targetKey === ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY
              ? { [ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY]: token }
              : {}),
          },
          () => {
            try {
              chrome.tabs.remove(tabId);
            } catch {
              // best-effort close
            }
          }
        );
      }
    );
  } catch {
    // ignore malformed URLs
  }
});

// ---------------------------------------------------------------------------
// Walmart bulk import job controller
// ---------------------------------------------------------------------------

const WALMART_ORDER_DETAIL_STORAGE_KEY = "walmartOrderDetail";
const WALMART_BULK_PORTS = new Set();

function broadcastToBulkPorts(message) {
  WALMART_BULK_PORTS.forEach((port) => {
    try {
      port.postMessage(message);
    } catch {
      // ignore
    }
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function getOrderManagerApiBaseUrlAsync() {
  return new Promise((resolve) => {
    try {
      const wm = globalThis.OrderManagerWalmart;
      if (wm && typeof wm.getOrderManagerApiBaseUrl === "function") {
        wm.getOrderManagerApiBaseUrl((v) => resolve(v));
        return;
      }
    } catch {
      // ignore
    }

    // Fallback: read base URL directly from storage (works even if lib/walmart.js
    // could not be imported in this background context).
    try {
      const ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY = "orderManagerActiveServer"; // "prod" | "dev"
      const ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY = "orderManagerDevEnabled";
      const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY = "orderManagerApiBaseUrl";
      const ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY = "orderManagerProdApiBaseUrl";
      const ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY = "orderManagerDevApiBaseUrl";

      chrome.storage.local.get(
        [
          ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY,
          ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY,
          ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY,
          ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY,
          ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY,
        ],
        (data) => {
          if (chrome.runtime && chrome.runtime.lastError) {
            resolve(null);
            return;
          }

          const serverRaw =
            data && data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]
              ? String(data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]).trim()
              : "prod";
          const server = serverRaw === "dev" ? "dev" : "prod";

          if (server === "dev") {
            const enabled = !!(data && data[ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]);
            const devBase =
              data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
                ? String(data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]).trim()
                : "";
            resolve(enabled && devBase ? devBase : null);
            return;
          }

          const prodBase =
            data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]
              ? String(data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]).trim()
              : data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]
                ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]).trim()
                : "";
          resolve(prodBase || null);
        }
      );
    } catch {
      resolve(null);
    }
  });
}

function normalizeWalmartOrderDetailPayloadSafe(payload, sourceUrl) {
  const wm = globalThis.OrderManagerWalmart;
  if (wm && typeof wm.normalizeWalmartOrderDetailPayload === "function") {
    return wm.normalizeWalmartOrderDetailPayload(payload, sourceUrl);
  }
  // Fallback: keep background self-sufficient even if importScripts fails.
  return normalizeWalmartOrderDetailPayloadFallback(payload, sourceUrl);
}

function normalizeWalmartOrderDetailPayloadFallback(payload, sourceUrl) {
  if (!payload || typeof payload !== "object") {
    throw new Error("Missing Walmart order payload.");
  }

  const raw = payload.raw || null;
  let order = payload.order || null;

  if (!order && raw && raw.props && raw.props.pageProps) {
    try {
      const pageProps = raw.props.pageProps;
      if (pageProps.initialData && pageProps.initialData.data && pageProps.initialData.data.order) {
        order = pageProps.initialData.data.order;
      }
    } catch {
      // ignore
    }
  }

  if (!order || !order.id) {
    throw new Error("Walmart order structure not recognized.");
  }

  const customer = order.customer || {};
  const groups = Array.isArray(order.groups_2101) ? order.groups_2101 : [];
  const paymentMethodsRaw = Array.isArray(order.paymentMethods) ? order.paymentMethods : [];

  const shipments = [];
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i] || {};
    const status = g.status || {};
    const shipment = g.shipment || {};
    const subtotal = g.subtotal || {};

    shipments.push({
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
        subtotal: typeof subtotal.value === "number" ? subtotal.value : null,
      },
    });
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

      let unitPriceVal =
        typeof itemPrice.value === "number"
          ? itemPrice.value
          : typeof unitPriceObj.value === "number"
            ? unitPriceObj.value
            : null;
      if (unitPriceVal == null && linePriceVal != null && qty > 0) {
        unitPriceVal = linePriceVal / qty;
      }
      const lineTotal = linePriceVal != null ? linePriceVal : unitPriceVal != null ? unitPriceVal * qty : null;

      const variants = Array.isArray(item.selectedVariants)
        ? item.selectedVariants.map(function (v) {
            return {
              name: v && v.name ? String(v.name) : null,
              value: v && v.value ? String(v.value) : null,
            };
          })
        : [];

      items.push({
        logicalItemId: item.id != null ? String(item.id) : item.usItemId || null,
        externalSku: productInfo.usItemId || null,
        externalOfferId: productInfo.offerId || null,
        name: productInfo.name || null,
        productUrl: productInfo.canonicalUrl || null,
        imageUrl: productInfo.imageInfo && productInfo.imageInfo.thumbnailUrl ? productInfo.imageInfo.thumbnailUrl : null,
        variants: variants,
        quantities: {
          ordered: typeof item.quantity === "number" ? item.quantity : null,
        },
        pricing: {
          unitPrice: unitPriceVal,
          linePrice: linePriceVal,
          lineTotal: typeof lineTotal === "number" ? lineTotal : null,
          strikethroughPrice: typeof strikethroughPrice.value === "number" ? strikethroughPrice.value : null,
          discounts: [],
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
          returnEligibilityMessage: item.returnEligibilityMessage || null,
        },
      });
    }
  }

  const itemCancelReasons = Array.isArray(order.itemCancelReasons)
    ? order.itemCancelReasons.map(function (r) {
        return {
          code: r && r.subReasonCode != null ? String(r.subReasonCode) : null,
          description: r && typeof r.subDescription === "string" ? r.subDescription : null,
        };
      })
    : [];

  const totals = {
    itemCount: typeof order.itemCount === "number" ? order.itemCount : null,
    subtotal: shippingGroup.subtotal && typeof shippingGroup.subtotal.value === "number" ? shippingGroup.subtotal.value : null,
    grandTotal:
      order.priceDetails && order.priceDetails.grandTotal && typeof order.priceDetails.grandTotal.value === "number"
        ? order.priceDetails.grandTotal.value
        : null,
  };

  const externalUrl = sourceUrl || payload.url || null;

  return {
    store: "walmart",
    source: "browser-extension",
    capturedAt: new Date().toISOString(),
    externalOrder: {
      id: String(order.id),
      orderDate: order.orderDate || null,
      timezone: order.timezone || null,
      url: externalUrl || null,
    },
    customer: {
      email: customer.email || null,
      firstName: customer.firstName || null,
      lastName: customer.lastName || null,
    },
    shippingAddress: shippingAddress,
    shipments: shipments,
    paymentMethods: paymentMethodsRaw.map(function (pm) {
      const description = typeof pm.description === "string" ? pm.description : null;
      let last4 = null;
      if (description) {
        const m = /(\d{4})\b/.exec(description);
        if (m) last4 = m[1];
      }
      return {
        description: description,
        cardType: typeof pm.cardType === "string" ? pm.cardType : null,
        paymentType: typeof pm.paymentType === "string" ? pm.paymentType : null,
        last4: last4,
      };
    }),
    items: items,
    cancellations: {
      orderLevel: Array.isArray(order.cancelReasons) ? order.cancelReasons : [],
      itemLevelReasons: itemCancelReasons,
    },
    totals: totals,
  };
}

async function clearWalmartDetailStorage() {
  await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(WALMART_ORDER_DETAIL_STORAGE_KEY, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function waitForWalmartDetail(orderNumber, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const current = await new Promise((resolve) => {
      try {
        chrome.storage.local.get(WALMART_ORDER_DETAIL_STORAGE_KEY, (s) => {
          resolve(s && s[WALMART_ORDER_DETAIL_STORAGE_KEY] ? s[WALMART_ORDER_DETAIL_STORAGE_KEY] : null);
        });
      } catch {
        resolve(null);
      }
    });

    if (current && current.payload && current.url && String(current.url).includes(String(orderNumber))) {
      return current;
    }
    await sleep(500);
  }
  throw new Error("Timed out waiting for Walmart order detail payload for " + String(orderNumber));
}

async function ensureTabExists(tabId) {
  return await new Promise((resolve) => {
    try {
      chrome.tabs.get(tabId, (tab) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          resolve(null);
          return;
        }
        resolve(tab || null);
      });
    } catch {
      resolve(null);
    }
  });
}

async function createWalmartScrapeTab(cookieStoreId) {
  return await new Promise((resolve, reject) => {
    try {
      const createProps = { url: "https://www.walmart.com/orders", active: false };
      // Firefox Multi-Account Containers support: open in same container.
      // Requires "cookies" permission to set cookieStoreId.
      if (cookieStoreId) {
        createProps.cookieStoreId = String(cookieStoreId);
      }
      chrome.tabs.create(createProps, (tab) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message || "Could not create Walmart tab."));
          return;
        }
        if (!tab || typeof tab.id !== "number") {
          reject(new Error("Could not create Walmart tab."));
          return;
        }
        resolve(tab.id);
      });
    } catch (e) {
      reject(e);
    }
  });
}

async function closeTab(tabId) {
  if (typeof tabId !== "number") return;
  try {
    chrome.tabs.remove(tabId);
  } catch {
    // ignore
  }
}

async function navigateTab(tabId, url) {
  await new Promise((resolve) => {
    try {
      chrome.tabs.update(tabId, { url }, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function postBulkSession(baseUrl, orders) {
  let rootUrl = String(baseUrl || "").trim();
  if (!rootUrl) throw new Error("Order Manager base URL is not configured.");
  if (rootUrl.endsWith("/")) rootUrl = rootUrl.slice(0, -1);

  const hasApiSuffix = rootUrl.endsWith("/api");
  const appBase = hasApiSuffix ? rootUrl.slice(0, -4) : rootUrl;
  const apiBase = hasApiSuffix ? rootUrl : rootUrl + "/api";
  const apiUrl = apiBase + "/integrations/stores/orders/bulk-session";

  const resp = await fetch(apiUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ orders }),
  });

  if (!resp.ok) {
    let msg = "Server returned " + resp.status + " " + resp.statusText;
    try {
      const errBody = await resp.json();
      if (errBody && errBody.detail) msg = String(errBody.detail);
    } catch {
      // ignore
    }
    throw new Error(msg);
  }

  const data = await resp.json();
  const token = data && data.token;
  if (!token) throw new Error("Missing token from server response.");
  return { appBase, token: String(token) };
}

async function openReviewTab(url) {
  await new Promise((resolve) => {
    try {
      chrome.tabs.create({ url }, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function collectOrderNumbersFromOrdersListTab(tabId, maxPages) {
  const safeMaxPages = typeof maxPages === "number" && Number.isFinite(maxPages) ? maxPages : parseInt(String(maxPages || "1"), 10);
  const pages = Number.isFinite(safeMaxPages) && safeMaxPages > 0 ? Math.min(Math.floor(safeMaxPages), 50) : 1;

  const resp = await new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(
        tabId,
        { store: "walmart", type: "walmartCollectOrdersAcrossPages", maxPages: pages },
        (r) => {
          if (chrome.runtime && chrome.runtime.lastError) {
            resolve({ success: false, error: chrome.runtime.lastError.message || "Unknown communication error." });
            return;
          }
          resolve(r || { success: false, error: "No response" });
        }
      );
    } catch (e) {
      resolve({ success: false, error: String(e && e.message ? e.message : e) });
    }
  });

  if (!resp || resp.success !== true) {
    throw new Error(resp && resp.error ? String(resp.error) : "Could not collect orders from Walmart page.");
  }

  const orders = Array.isArray(resp.orders) ? resp.orders : [];
  const orderNumbers = orders.map((o) => (o && o.orderNumber ? String(o.orderNumber) : null)).filter(Boolean);
  return orderNumbers;
}

function attachBulkPortHandlers(port) {
  let running = false;
  let cancelled = false;
  let scrapeTabId = null;

  port.onMessage.addListener(async (msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.store !== "walmart") return;

    if (msg.type === "cancel") {
      cancelled = true;
      try {
        await closeTab(scrapeTabId);
      } catch {
        // ignore
      }
      try {
        port.postMessage({ type: "jobCancelled" });
      } catch {
        // ignore
      }
      return;
    }

    if (msg.type !== "start") return;
    if (running) return;
    running = true;

    let orderNumbers = Array.isArray(msg.orderNumbers) ? msg.orderNumbers.map((n) => String(n)).filter(Boolean) : [];

    const baseUrl = await getOrderManagerApiBaseUrlAsync();
    if (!baseUrl) {
      port.postMessage({ type: "jobError", error: "Order Manager base URL is not configured. Configure it in Settings first." });
      return;
    }

    // Best-effort: ensure popup source tab still exists (but do not touch it).
    if (typeof msg.sourceTabId === "number") {
      await ensureTabExists(msg.sourceTabId);
    }

    // If order numbers weren't provided yet, collect them from the orders list tab now.
    if (orderNumbers.length === 0) {
      if (typeof msg.sourceTabId !== "number") {
        port.postMessage({ type: "jobError", error: "Missing source tab id (Walmart orders list tab)." });
        return;
      }

      try {
        port.postMessage({ type: "collectingOrderNumbers" });
        orderNumbers = await collectOrderNumbersFromOrdersListTab(msg.sourceTabId, msg.maxPages);
      } catch (e) {
        port.postMessage({ type: "jobError", error: String(e && e.message ? e.message : e) });
        return;
      }

      if (orderNumbers.length === 0) {
        port.postMessage({ type: "jobError", error: "No orders found on these pages." });
        return;
      }

      port.postMessage({ type: "orderNumbersReady", total: orderNumbers.length });
    }

    // If we can read the container from the orders-list tab (Firefox), create the scraping tab in the same container.
    let cookieStoreId = null;
    if (typeof msg.sourceTabId === "number") {
      try {
        const t = await ensureTabExists(msg.sourceTabId);
        if (t && t.cookieStoreId) cookieStoreId = String(t.cookieStoreId);
      } catch {
        cookieStoreId = null;
      }
    }

    port.postMessage({ type: "jobStarted" });

    try {
      scrapeTabId = await createWalmartScrapeTab(cookieStoreId);

      const collectedPayloads = [];

      for (let i = 0; i < orderNumbers.length; i++) {
        if (cancelled) {
          port.postMessage({ type: "jobCancelled" });
          return;
        }

        const orderNumber = orderNumbers[i];
        port.postMessage({ type: "orderStatus", status: "pending", orderNumber });

        try {
          await clearWalmartDetailStorage();
          await navigateTab(scrapeTabId, "https://www.walmart.com/orders/" + encodeURIComponent(orderNumber));

          if (cancelled) {
            port.postMessage({ type: "jobCancelled" });
            return;
          }

          const payloadObj = await waitForWalmartDetail(orderNumber, 20000);
          const body = normalizeWalmartOrderDetailPayloadSafe(payloadObj.payload, payloadObj.url);
          collectedPayloads.push(body);
          port.postMessage({ type: "orderStatus", status: "ok", orderNumber });
        } catch (e) {
          port.postMessage({
            type: "orderStatus",
            status: "error",
            orderNumber,
            error: String(e && e.message ? e.message : e),
          });
        }

        if (i < orderNumbers.length - 1) {
          await sleep(1500);
        }
      }

      if (cancelled) {
        port.postMessage({ type: "jobCancelled" });
        return;
      }

      if (collectedPayloads.length > 0) {
        const { appBase, token } = await postBulkSession(baseUrl, collectedPayloads);
        const bulkUrl = appBase + "/import-review/bulk?token=" + encodeURIComponent(token);
        await openReviewTab(bulkUrl);
        port.postMessage({ type: "reviewReady", url: bulkUrl });
      } else {
        port.postMessage({ type: "jobError", error: "No orders were successfully collected." });
        return;
      }

      port.postMessage({ type: "jobDone" });
    } catch (e) {
      if (cancelled) {
        try {
          port.postMessage({ type: "jobCancelled" });
        } catch {
          // ignore
        }
      } else {
        port.postMessage({ type: "jobError", error: String(e && e.message ? e.message : e) });
      }
    } finally {
      await closeTab(scrapeTabId);
    }
  });
}

if (chrome && chrome.runtime && chrome.runtime.onConnect) {
  chrome.runtime.onConnect.addListener((port) => {
    try {
      if (!port || port.name !== "walmartBulkImport") return;
      try {
        WALMART_BULK_PORTS.add(port);
      } catch {
        // ignore
      }
      try {
        port.onDisconnect.addListener(() => {
          try {
            WALMART_BULK_PORTS.delete(port);
          } catch {
            // ignore
          }
        });
      } catch {
        // ignore
      }
      attachBulkPortHandlers(port);
    } catch {
      // ignore
    }
  });
}

// Receive per-page collection progress from the Walmart orders list content script bridge
// and forward to the bulk progress tab.
if (chrome && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg) => {
    try {
      if (!msg || msg.store !== "walmart") return;
      if (msg.type !== "walmartCollectOrdersProgress") return;

      broadcastToBulkPorts({
        type: "orderNumbersPageProgress",
        page: msg.page,
        extracted: msg.extracted,
        total: msg.total,
        pagesCollected: msg.pagesCollected,
        maxPages: msg.maxPages,
      });
    } catch {
      // ignore
    }
  });
}
