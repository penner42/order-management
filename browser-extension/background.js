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
try {
  // eslint-disable-next-line no-undef
  importScripts("lib/costco.js");
} catch {
  // ignore (Firefox may not support importScripts in some contexts)
}
try {
  // eslint-disable-next-line no-undef
  importScripts("lib/amazon.js");
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
const COSTCO_ORDER_DETAIL_STORAGE_KEY = "costcoOrderDetailsGraphqlCapture";
const WALMART_BULK_PORTS = new Set();
const COSTCO_BULK_PORTS = new Set();
const AMAZON_BULK_PORTS = new Set();

const AMAZON_ORDER_DETAIL_STORAGE_KEY = "amazonOrderDetail";
const AMAZON_ACCOUNT_EMAIL_STORAGE_KEY = "amazonAccountEmail";
const AMAZON_CONTENT_SCRIPT_FILES = [
  "lib/amazon.js",
  "stores/amazon/selectors.js",
  "stores/amazon/dom.js",
  "stores/amazon/capture.js",
];

/** @type {Map<number, { script: boolean, list: boolean, detail: boolean, url: string | null }>} */
const AMAZON_TAB_READY_STATE = new Map();

/** @type {Map<number, { script: Set<(ok: boolean) => void>, list: Set<(ok: boolean) => void>, detail: Set<(ok: boolean) => void> }>} */
const AMAZON_TAB_SIGNAL_WAITERS = new Map();

/** @type {Map<string, Set<(record: { url: string | null, payload: object } | null) => void>>} */
const AMAZON_DETAIL_CAPTURE_WAITERS = new Map();

function clearAmazonTabSignals(tabId) {
  AMAZON_TAB_READY_STATE.delete(tabId);
  AMAZON_TAB_SIGNAL_WAITERS.delete(tabId);
}

function resolveAmazonTabSignalWaiters(tabId, signal, ok) {
  const waiters = AMAZON_TAB_SIGNAL_WAITERS.get(tabId);
  if (!waiters || !waiters[signal]) return;
  waiters[signal].forEach((resolve) => resolve(ok));
  waiters[signal].clear();
}

function markAmazonTabSignal(tabId, signal, url) {
  let state = AMAZON_TAB_READY_STATE.get(tabId);
  if (!state) {
    state = { script: false, list: false, detail: false, url: null };
    AMAZON_TAB_READY_STATE.set(tabId, state);
  }
  state[signal] = true;
  if (url) state.url = url;
  resolveAmazonTabSignalWaiters(tabId, signal, true);
}

function waitForAmazonTabSignal(tabId, signal, timeoutMs, port) {
  const state = AMAZON_TAB_READY_STATE.get(tabId);
  if (state && state[signal]) return Promise.resolve(true);

  return new Promise((resolve) => {
    let waiters = AMAZON_TAB_SIGNAL_WAITERS.get(tabId);
    if (!waiters) {
      waiters = { script: new Set(), list: new Set(), detail: new Set() };
      AMAZON_TAB_SIGNAL_WAITERS.set(tabId, waiters);
    }

    let settled = false;
    let heartbeatTimer = null;
    let timeoutTimer = null;

    const done = (ok) => {
      if (settled) return;
      settled = true;
      waiters[signal].delete(done);
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      if (timeoutTimer != null) clearTimeout(timeoutTimer);
      resolve(!!ok);
    };
    waiters[signal].add(done);

    if (port) {
      heartbeatTimer = setInterval(() => {
        touchServiceWorker();
        try {
          port.postMessage({ type: "heartbeat" });
        } catch {
          // ignore
        }
      }, 4000);
    }

    const timeout = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 15000;
    timeoutTimer = setTimeout(() => done(false), timeout);
  });
}

function resolveAmazonDetailCaptureWaiters(orderId, record) {
  const target = String(orderId || "").trim();
  if (!target) return;
  const waiters = AMAZON_DETAIL_CAPTURE_WAITERS.get(target);
  if (!waiters) return;
  waiters.forEach((resolve) => {
    try {
      resolve(record);
    } catch {
      // ignore
    }
  });
  AMAZON_DETAIL_CAPTURE_WAITERS.delete(target);
}

function handleAmazonContentScriptSignal(msg, sender) {
  const tabId = sender && sender.tab && sender.tab.id != null ? sender.tab.id : null;

  if (msg.type === "amazonContentScriptReady") {
    if (tabId == null) return;
    markAmazonTabSignal(tabId, "script", msg.url ? String(msg.url) : null);
    return;
  }

  if (msg.type === "amazonPageReady") {
    if (tabId == null) return;
    const kind = msg.pageKind ? String(msg.pageKind) : "";
    if (kind === "list") {
      markAmazonTabSignal(tabId, "list", msg.url ? String(msg.url) : null);
    } else if (kind === "detail") {
      markAmazonTabSignal(tabId, "detail", msg.url ? String(msg.url) : null);
    }
    return;
  }

  if (msg.type === "amazonDetailCaptured") {
    const payload = msg.payload && typeof msg.payload === "object" ? msg.payload : null;
    const orderId =
      msg.orderId != null
        ? String(msg.orderId).trim()
        : payload && payload.orderId != null
          ? String(payload.orderId).trim()
          : "";
    if (!orderId || !payload) return;

    const record = {
      url: msg.url ? String(msg.url) : null,
      payload,
    };
    resolveAmazonDetailCaptureWaiters(orderId, record);
    if (tabId != null) {
      markAmazonTabSignal(tabId, "detail", record.url);
    }
  }
}

if (chrome && chrome.tabs && chrome.tabs.onUpdated) {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    try {
      if (changeInfo && changeInfo.status === "loading") {
        clearAmazonTabSignals(tabId);
      }
    } catch {
      // ignore
    }
  });
}

if (chrome && chrome.tabs && chrome.tabs.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    try {
      clearAmazonTabSignals(tabId);
    } catch {
      // ignore
    }
  });
}

function broadcastToBulkPorts(message) {
  WALMART_BULK_PORTS.forEach((port) => {
    try {
      port.postMessage(message);
    } catch {
      // ignore
    }
  });
}

function broadcastToCostcoBulkPorts(message) {
  COSTCO_BULK_PORTS.forEach((port) => {
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

function normalizeCostcoOrdersGraphqlPayloadSafe(payload, sourceUrl) {
  const c = globalThis.OrderManagerCostco;
  if (c && typeof c.normalizeCostcoOrdersGraphqlPayload === "function") {
    return c.normalizeCostcoOrdersGraphqlPayload(payload, sourceUrl);
  }
  // Fallback: keep background self-sufficient even if importScripts fails.
  return normalizeCostcoOrdersGraphqlPayloadFallback(payload, sourceUrl);
}

function normalizeAmazonOrderPayloadFallback(raw, sourceUrl, accountEmail) {
  function coerceString(v) {
    if (v == null) return null;
    if (typeof v === "string") return v.trim() || null;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return null;
  }

  if (!raw || typeof raw !== "object") {
    throw new Error("Missing Amazon order payload.");
  }

  const orderId = coerceString(raw.orderId);
  if (!orderId) {
    throw new Error("Amazon order structure not recognized (missing order id).");
  }

  const items = [];
  const rawItems = Array.isArray(raw.items) ? raw.items : [];
  for (let i = 0; i < rawItems.length; i++) {
    const it = rawItems[i] || {};
    const qty = typeof it.quantity === "number" && it.quantity > 0 ? it.quantity : 1;
    const unitPrice = typeof it.unitPrice === "number" ? it.unitPrice : null;
    const lineTotal =
      typeof it.lineTotal === "number" ? it.lineTotal : unitPrice != null ? unitPrice * qty : null;
    const itemShipmentId = coerceString(it.shipmentId);
    const shipmentSlices = itemShipmentId
      ? [{ shipmentId: itemShipmentId, quantity: qty, normalizedStatus: null }]
      : [];

    items.push({
      logicalItemId: coerceString(it.asin) || null,
      externalSku: coerceString(it.asin) || null,
      name: coerceString(it.name) || null,
      productUrl: coerceString(it.productUrl) || null,
      imageUrl: coerceString(it.imageUrl) || null,
      variants: [],
      quantities: { ordered: qty },
      pricing: {
        unitPrice,
        linePrice: lineTotal,
        lineTotal,
        strikethroughPrice: null,
        discounts: [],
      },
      status: {
        rawStatusCode: null,
        normalizedStatus: coerceString(raw.status) || null,
      },
      shipments: shipmentSlices,
      returnability: {
        isReturnable: false,
        returnEligibilityMessage: null,
      },
    });
  }

  const shipments = [];
  const rawShipments = Array.isArray(raw.shipments) ? raw.shipments : [];
  for (let si = 0; si < rawShipments.length; si++) {
    const s = rawShipments[si] || {};
    const st = s.status || {};
    const shipmentId =
      coerceString(s.shipmentId) || coerceString(s.trackingNumber) || "shipment-" + String(si);
    shipments.push({
      shipmentId,
      trackingNumber: coerceString(s.trackingNumber) || null,
      trackingUrl: coerceString(s.trackingUrl) || null,
      deliveryDate: coerceString(s.deliveryDate) || null,
      status: {
        rawStatusType: coerceString(st.rawStatusType) || coerceString(raw.status) || null,
        normalizedStatus: coerceString(st.message) || coerceString(st.rawStatusType) || null,
        message: coerceString(st.message) || null,
      },
    });
  }

  if (
    shipments.length === 1 &&
    shipments[0].shipmentId &&
    items.length > 0 &&
    items.every((item) => !item.shipments || item.shipments.length === 0)
  ) {
    const fallbackId = shipments[0].shipmentId;
    for (let i = 0; i < items.length; i++) {
      const qty = items[i].quantities && items[i].quantities.ordered ? items[i].quantities.ordered : 1;
      items[i].shipments = [{ shipmentId: fallbackId, quantity: qty, normalizedStatus: null }];
    }
  }

  const addr = raw.shippingAddress && typeof raw.shippingAddress === "object" ? raw.shippingAddress : null;
  const shippingAddress = addr
    ? {
        fullName: coerceString(addr.fullName) || null,
        addressLine1: coerceString(addr.addressLine1) || null,
        addressLine2: coerceString(addr.addressLine2) || null,
        city: coerceString(addr.city) || null,
        state: coerceString(addr.state) || null,
        postalCode: coerceString(addr.postalCode) || null,
        country: coerceString(addr.country) || null,
        phoneNumber: null,
      }
    : null;

  const totalsRaw = raw.totals && typeof raw.totals === "object" ? raw.totals : {};
  const totals = {
    subtotal: typeof totalsRaw.subtotal === "number" ? totalsRaw.subtotal : null,
    grandTotal:
      typeof totalsRaw.grandTotal === "number"
        ? totalsRaw.grandTotal
        : typeof raw.totalAmount === "number"
          ? raw.totalAmount
          : null,
  };

  const paymentMethods = [];
  const rawPm = Array.isArray(raw.paymentMethods) ? raw.paymentMethods : [];
  for (let pi = 0; pi < rawPm.length; pi++) {
    const pm = rawPm[pi] || {};
    paymentMethods.push({
      description: coerceString(pm.description) || null,
      cardType: coerceString(pm.cardType) || null,
      paymentType: null,
      last4: coerceString(pm.last4) || null,
    });
  }

  const payload = {
    store: "amazon",
    source: "browser-extension",
    capturedAt: new Date().toISOString(),
    externalOrder: {
      id: orderId,
      orderDate: coerceString(raw.orderDate) || null,
      url: sourceUrl || coerceString(raw.detailUrl) || null,
      statusType: coerceString(raw.status) || null,
    },
    customer: {
      email: coerceString(accountEmail) || null,
    },
    shippingAddress,
    shipments,
    items,
    paymentMethods,
    totals,
  };

  if (typeof totalsRaw.orderDiscount === "number" && totalsRaw.orderDiscount > 0) {
    payload.orderDiscount = totalsRaw.orderDiscount;
  }

  return payload;
}

function ensureAmazonLibLoaded() {
  const am = globalThis.OrderManagerAmazon;
  if (am && typeof am.normalizeAmazonOrderPayload === "function") return true;
  try {
    // eslint-disable-next-line no-undef
    importScripts("lib/amazon.js");
  } catch {
    try {
      if (chrome.runtime && typeof chrome.runtime.getURL === "function") {
        // eslint-disable-next-line no-undef
        importScripts(chrome.runtime.getURL("lib/amazon.js"));
      }
    } catch {
      // ignore
    }
  }
  const loaded = globalThis.OrderManagerAmazon;
  return !!(loaded && typeof loaded.normalizeAmazonOrderPayload === "function");
}

function normalizeAmazonOrderPayloadSafe(payload, sourceUrl, accountEmail) {
  if (ensureAmazonLibLoaded()) {
    return globalThis.OrderManagerAmazon.normalizeAmazonOrderPayload(payload, sourceUrl, accountEmail);
  }
  return normalizeAmazonOrderPayloadFallback(payload, sourceUrl, accountEmail);
}

function normalizeCostcoOrdersGraphqlPayloadFallback(graphqlPayload, sourceUrl) {
  function coerceString(v) {
    if (v == null) return null;
    if (typeof v === "string") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return null;
  }

  function normalizeIsoOrNull(v) {
    const s = coerceString(v);
    if (!s || !s.trim()) return null;
    return s.trim();
  }

  function safeNumber(v) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (
      v &&
      typeof v === "object" &&
      typeof v.parsedValue === "number" &&
      Number.isFinite(v.parsedValue)
    ) {
      return v.parsedValue;
    }
    if (typeof v === "string" && v.trim()) {
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  }

  function extractOrdersFromCostcoGraphql(payload) {
    if (!payload || typeof payload !== "object") return [];
    const go = payload.data && payload.data.getOnlineOrders;
    if (!Array.isArray(go) || go.length === 0) return [];
    const first = go[0] || {};
    const orders = Array.isArray(first.bcOrders) ? first.bcOrders : [];
    return orders;
  }

  const orders = extractOrdersFromCostcoGraphql(graphqlPayload);
  const capturedAt = new Date().toISOString();
  const normalized = [];

  const externalUrl =
    sourceUrl ||
    (typeof document !== "undefined"
      ? document.location && document.location.href
      : null) ||
    null;

  for (let oi = 0; oi < orders.length; oi++) {
    const o = orders[oi] || {};
    const orderNumber = coerceString(o.orderNumber);
    if (!orderNumber || !orderNumber.trim()) continue;

    const orderPlacedDate = normalizeIsoOrNull(o.orderPlacedDate);
    const status = coerceString(o.status);
    const orderTotal = safeNumber(o.orderTotal);

    const shipmentsById = {};
    const shipmentsList = [];
    const itemsList = [];

    const lineItems = Array.isArray(o.orderLineItems) ? o.orderLineItems : [];
    for (let li = 0; li < lineItems.length; li++) {
      const item = lineItems[li] || {};
      const itemName =
        coerceString(item.itemDescription) ||
        coerceString(item.itemNumber) ||
        "Item";
      const logicalItemId =
        coerceString(item.orderLineItemId) ||
        (item.lineNumber != null ? String(item.lineNumber) : null) ||
        null;

      const shipmentSlices = [];
      const shipments = Array.isArray(item.shipment) ? item.shipment : [];
      for (let si = 0; si < shipments.length; si++) {
        const s = shipments[si] || {};
        const shipmentId =
          coerceString(s.shipmentId) ||
          coerceString(s.packageNumber) ||
          coerceString(s.trackingNumber);
        if (!shipmentId) continue;

        const trackingNumber = coerceString(s.trackingNumber);
        const trackingUrl = coerceString(s.trackingSiteUrl);

        const deliveredDate = normalizeIsoOrNull(s.deliveredDate);
        const estimatedArrivalDate = normalizeIsoOrNull(s.estimatedArrivalDate);
        const deliveryDate =
          deliveredDate || estimatedArrivalDate || normalizeIsoOrNull(item.deliveryDate);

        const shipmentStatus =
          coerceString(s.status) || coerceString(item.status) || null;

        if (!shipmentsById[shipmentId]) {
          shipmentsById[shipmentId] = {
            shipmentId,
            trackingNumber: trackingNumber || null,
            trackingUrl: trackingUrl || null,
            deliveryDate: deliveryDate || null,
            status: {
              rawStatusType: shipmentStatus,
              message: shipmentStatus,
            },
          };
        }

        shipmentSlices.push({ shipmentId, quantity: 1 });
      }

      itemsList.push({
        logicalItemId,
        externalSku: coerceString(item.itemNumber) || coerceString(item.itemId) || null,
        name: itemName || null,
        productUrl: null,
        imageUrl: null,
        variants: [],
        quantities: { ordered: 1 },
        pricing: {
          unitPrice: null,
          linePrice: null,
          lineTotal: null,
          strikethroughPrice: null,
          discounts: [],
        },
        status: {
          rawStatusCode: coerceString(item.status) || null,
          normalizedStatus: null,
        },
        shipments: shipmentSlices,
        returnability: {
          isReturnable: !!item.orderReturnAllowed,
          returnEligibilityMessage: null,
        },
      });
    }

    for (const sid in shipmentsById) {
      shipmentsList.push(shipmentsById[sid]);
    }

    normalized.push({
      store: "costco",
      source: "browser-extension",
      capturedAt,
      externalOrder: {
        id: String(orderNumber).trim(),
        orderDate: orderPlacedDate,
        url: externalUrl,
        statusType: status || null,
      },
      customer: {
        email: coerceString(o.emailAddress) || null,
        firstName: null,
        lastName: null,
      },
      shippingAddress: null,
      shipments: shipmentsList,
      paymentMethods: [],
      items: itemsList,
      cancellations: {
        orderLevel: [],
        itemLevelReasons: [],
      },
      totals: {
        grandTotal: orderTotal,
      },
    });
  }

  return normalized;
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

async function clearCostcoDetailStorage() {
  await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(COSTCO_ORDER_DETAIL_STORAGE_KEY, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function waitForCostcoDetail(orderHeaderId, timeoutMs) {
  const start = Date.now();
  const target = String(orderHeaderId || "").trim();
  const t = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 25000;

  while (Date.now() - start < t) {
    const current = await new Promise((resolve) => {
      try {
        chrome.storage.local.get(COSTCO_ORDER_DETAIL_STORAGE_KEY, (s) => {
          resolve(s && s[COSTCO_ORDER_DETAIL_STORAGE_KEY] ? s[COSTCO_ORDER_DETAIL_STORAGE_KEY] : null);
        });
      } catch {
        resolve(null);
      }
    });

    if (current && current.payload) {
      const id = current.orderHeaderId != null ? String(current.orderHeaderId).trim() : "";
      if (!target || id === target) return current;
    }
    await sleep(500);
  }

  throw new Error("Timed out waiting for Costco order detail payload for " + String(orderHeaderId));
}

async function clearAmazonDetailStorage() {
  await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(AMAZON_ORDER_DETAIL_STORAGE_KEY, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function getAmazonAccountEmailAsync() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.get(AMAZON_ACCOUNT_EMAIL_STORAGE_KEY, (s) => {
        const row = s && s[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY];
        const email = row && row.email ? String(row.email).trim() : "";
        resolve(email || null);
      });
    } catch {
      resolve(null);
    }
  });
}

function touchServiceWorker() {
  try {
    if (chrome.runtime && typeof chrome.runtime.getPlatformInfo === "function") {
      chrome.runtime.getPlatformInfo(() => {});
    }
  } catch {
    // ignore
  }
}

async function keepaliveSleep(ms, port, meta) {
  const end = Date.now() + (typeof ms === "number" && ms > 0 ? ms : 0);
  while (Date.now() < end) {
    touchServiceWorker();
    if (port) {
      try {
        port.postMessage({ type: "heartbeat", ...(meta && typeof meta === "object" ? meta : {}) });
      } catch {
        // ignore
      }
    }
    await sleep(Math.min(4000, Math.max(0, end - Date.now())));
  }
}

async function waitForAmazonDetailCaptured(orderId, timeoutMs, port, lastError) {
  const target = String(orderId || "").trim();
  const t = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 25000;

  const existing = await readAmazonDetailFromStorage(orderId);
  if (existing) return existing;

  return new Promise((resolve, reject) => {
    let settled = false;
    let heartbeatTimer = null;
    let timeoutTimer = null;
    let storageListener = null;

    const cleanup = () => {
      if (storageListener && chrome.storage && chrome.storage.onChanged) {
        try {
          chrome.storage.onChanged.removeListener(storageListener);
        } catch {
          // ignore
        }
      }
      const waiters = AMAZON_DETAIL_CAPTURE_WAITERS.get(target);
      if (waiters) {
        waiters.delete(onCaptured);
        if (waiters.size === 0) AMAZON_DETAIL_CAPTURE_WAITERS.delete(target);
      }
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      if (timeoutTimer != null) clearTimeout(timeoutTimer);
    };

    const finishOk = (record) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(record);
    };

    const finishErr = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(
        new Error(
          "Timed out waiting for Amazon order detail payload for " +
            target +
            (lastError ? " (" + lastError + ")" : "")
        )
      );
    };

    const onCaptured = (record) => {
      if (!record || !record.payload) return;
      const id = extractAmazonOrderIdFromPayload(record.payload);
      if (target && id !== target) return;
      finishOk(record);
    };

    let waiters = AMAZON_DETAIL_CAPTURE_WAITERS.get(target);
    if (!waiters) {
      waiters = new Set();
      AMAZON_DETAIL_CAPTURE_WAITERS.set(target, waiters);
    }
    waiters.add(onCaptured);

    if (chrome.storage && chrome.storage.onChanged) {
      storageListener = (changes, area) => {
        if (area !== "local" || !changes[AMAZON_ORDER_DETAIL_STORAGE_KEY]) return;
        readAmazonDetailFromStorage(orderId).then((current) => {
          if (current) finishOk(current);
        });
      };
      chrome.storage.onChanged.addListener(storageListener);
    }

    if (port) {
      heartbeatTimer = setInterval(() => {
        touchServiceWorker();
        try {
          port.postMessage({ type: "heartbeat", orderNumber: target || null });
        } catch {
          // ignore
        }
      }, 4000);
    }

    timeoutTimer = setTimeout(finishErr, t);
  });
}

function withAmazonDisableCsdUrl(url) {
  const am = globalThis.OrderManagerAmazon;
  if (am && typeof am.withDisableCsdParam === "function") {
    return am.withDisableCsdParam(url);
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

async function ensureAmazonCsdDisabledCookie(origin, cookieStoreId) {
  if (!chrome.cookies || typeof chrome.cookies.set !== "function") return;
  const base = String(origin || "https://www.amazon.com").replace(/\/$/, "");
  await new Promise((resolve) => {
    try {
      const details = {
        url: base + "/",
        name: "csd-key",
        value: "disabled",
        path: "/",
      };
      if (cookieStoreId) details.cookieStoreId = String(cookieStoreId);
      chrome.cookies.set(details, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

function isAmazonContentScriptConnectionError(resp) {
  if (!resp || typeof resp !== "object") return true;
  if (resp.success === true) return false;
  const err = String(resp.error || "").toLowerCase();
  return (
    err.includes("receiving end does not exist") ||
    err.includes("could not establish connection") ||
    err.includes("message port closed")
  );
}

const AMAZON_PROGRAMMATIC_INJECT_BLOCKED = new Set();

function isAmazonProgrammaticInjectBlockedError(error) {
  const err = String(error || "").toLowerCase();
  return err.includes("missing host permission");
}

async function ensureAmazonContentScripts(tabId) {
  if (AMAZON_PROGRAMMATIC_INJECT_BLOCKED.has(tabId)) {
    return { ok: false, error: "programmatic inject skipped (container tab)", skipped: true };
  }
  if (!chrome.scripting || typeof chrome.scripting.executeScript !== "function") {
    return { ok: false, error: "scripting API unavailable" };
  }
  const tab = await ensureTabExists(tabId);
  if (!tab || !tab.url || !/^https:\/\/(.*\.)?amazon\.com/i.test(String(tab.url))) {
    return { ok: false, error: tab && tab.url ? "tab is not an Amazon URL: " + String(tab.url) : "tab missing" };
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: AMAZON_CONTENT_SCRIPT_FILES,
    });
    return { ok: true, url: tab.url };
  } catch (e) {
    const error = String(e && e.message ? e.message : e);
    if (isAmazonProgrammaticInjectBlockedError(error)) {
      AMAZON_PROGRAMMATIC_INJECT_BLOCKED.add(tabId);
    }
    return { ok: false, error, url: tab.url };
  }
}

async function readAmazonDetailFromStorage(orderId) {
  const target = String(orderId || "").trim();
  const current = await new Promise((resolve) => {
    try {
      chrome.storage.local.get(AMAZON_ORDER_DETAIL_STORAGE_KEY, (s) => {
        resolve(s && s[AMAZON_ORDER_DETAIL_STORAGE_KEY] ? s[AMAZON_ORDER_DETAIL_STORAGE_KEY] : null);
      });
    } catch {
      resolve(null);
    }
  });
  if (!current || !current.payload) return null;
  const id =
    current.payload.orderId != null
      ? String(current.payload.orderId).trim()
      : extractAmazonOrderIdFromPayload(current.payload);
  if (target && id !== target) return null;
  return current;
}

async function pingAmazonContentScript(tabId) {
  const ping = await sendAmazonTabMessage(tabId, { store: "amazon", type: "amazonPing" });
  return !!(ping && ping.success === true);
}

async function waitForAmazonContentScript(tabId, timeoutMs, port) {
  const timeout = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 15000;
  const deadline = Date.now() + timeout;

  if (await pingAmazonContentScript(tabId)) {
    markAmazonTabSignal(tabId, "script", null);
    return true;
  }

  while (Date.now() < deadline) {
    await ensureAmazonContentScripts(tabId);

    if (await pingAmazonContentScript(tabId)) {
      markAmazonTabSignal(tabId, "script", null);
      return true;
    }

    const remaining = deadline - Date.now();
    if (remaining <= 0) break;

    await waitForAmazonTabSignal(tabId, "script", Math.min(400, remaining), port);
    if (await pingAmazonContentScript(tabId)) {
      markAmazonTabSignal(tabId, "script", null);
      return true;
    }

    touchServiceWorker();
    if (port) {
      try {
        port.postMessage({ type: "heartbeat" });
      } catch {
        // ignore
      }
    }
  }

  return false;
}

async function waitForAmazonDetailOutcome(tabId, orderId, timeoutMs, port) {
  const stored = await readAmazonDetailFromStorage(orderId);
  if (stored && stored.payload) {
    return { kind: "capture", record: stored };
  }

  const timeout = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 35000;
  const target = String(orderId || "").trim();

  return new Promise((resolve) => {
    let settled = false;
    let heartbeatTimer = null;
    let timeoutTimer = null;
    let storageListener = null;
    let captureWaiter = null;

    const cleanup = () => {
      if (storageListener && chrome.storage && chrome.storage.onChanged) {
        try {
          chrome.storage.onChanged.removeListener(storageListener);
        } catch {
          // ignore
        }
      }
      if (captureWaiter) {
        const waiters = AMAZON_DETAIL_CAPTURE_WAITERS.get(target);
        if (waiters) {
          waiters.delete(captureWaiter);
          if (waiters.size === 0) AMAZON_DETAIL_CAPTURE_WAITERS.delete(target);
        }
      }
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      if (timeoutTimer != null) clearTimeout(timeoutTimer);
    };

    const finish = (value) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };

    captureWaiter = (record) => {
      if (!record || !record.payload) return;
      const id = extractAmazonOrderIdFromPayload(record.payload);
      if (target && id !== target) return;
      finish({ kind: "capture", record });
    };

    let captureWaiters = AMAZON_DETAIL_CAPTURE_WAITERS.get(target);
    if (!captureWaiters) {
      captureWaiters = new Set();
      AMAZON_DETAIL_CAPTURE_WAITERS.set(target, captureWaiters);
    }
    captureWaiters.add(captureWaiter);

    if (chrome.storage && chrome.storage.onChanged) {
      storageListener = (_changes, area) => {
        if (area !== "local") return;
        readAmazonDetailFromStorage(orderId).then((current) => {
          if (current && current.payload) finish({ kind: "capture", record: current });
        });
      };
      chrome.storage.onChanged.addListener(storageListener);
    }

    waitForAmazonTabSignal(tabId, "detail", timeout, port).then((ready) => {
      finish({ kind: "pageReady", ready: !!ready });
    });

    if (port) {
      heartbeatTimer = setInterval(() => {
        touchServiceWorker();
        try {
          port.postMessage({ type: "heartbeat", orderNumber: target || null });
        } catch {
          // ignore
        }
      }, 4000);
    }

    timeoutTimer = setTimeout(() => {
      finish({ kind: "timeout", ready: false });
    }, timeout);
  });
}

async function requestAmazonDetailFromTab(
  tabId,
  orderId,
  detailUrl,
  timeoutMs,
  port,
  listSummary,
  options
) {
  const opts = options && typeof options === "object" ? options : {};
  const deadline = Date.now() + (typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 45000);
  let lastError = null;

  const scriptReady = await waitForAmazonContentScript(tabId, Math.min(30000, timeoutMs || 45000));

  if (!scriptReady || !(await pingAmazonContentScript(tabId))) {
    throw new Error("Amazon content script is not reachable on the order detail tab.");
  }

  let detailReady = !!(AMAZON_TAB_READY_STATE.get(tabId) && AMAZON_TAB_READY_STATE.get(tabId).detail);
  if (!detailReady) {
    const detailWaitMs = Math.min(3000, Math.max(0, deadline - Date.now()));
    if (detailWaitMs > 0) {
      await waitForAmazonTabSignal(tabId, "detail", detailWaitMs, port);
      detailReady = !!(AMAZON_TAB_READY_STATE.get(tabId) && AMAZON_TAB_READY_STATE.get(tabId).detail);
    }
  }

  const storedEarly = await readAmazonDetailFromStorage(orderId);
  if (storedEarly && storedEarly.payload) {
    const storedId = extractAmazonOrderIdFromPayload(storedEarly.payload);
    if (!orderId || storedId === String(orderId).trim()) {
      return { url: storedEarly.url || detailUrl, payload: storedEarly.payload };
    }
  }

  for (let attempt = 0; attempt < 2 && Date.now() < deadline; attempt++) {
    const detailMsg = await sendAmazonTabMessage(tabId, {
      store: "amazon",
      type: "amazonParseCurrentDetailPage",
      skipTrackingEnrichment: !!opts.skipTrackingEnrichment,
      pageAlreadyReady: detailReady,
    });

    if (detailMsg && detailMsg.success === true && detailMsg.order && detailMsg.order.orderId) {
      const id = String(detailMsg.order.orderId).trim();
      if (!orderId || id === String(orderId).trim()) {
        return { url: detailUrl, payload: detailMsg.order };
      }
    }

    if (detailMsg && detailMsg.error) {
      lastError = String(detailMsg.error);
      if (!isAmazonContentScriptConnectionError(detailMsg)) {
        break;
      }
      await waitForAmazonContentScript(tabId, Math.min(5000, deadline - Date.now()), port);
      detailReady = false;
      continue;
    }

    break;
  }

  try {
    const remaining = Math.max(0, deadline - Date.now());
    const stored = await waitForAmazonDetailCaptured(orderId, Math.max(8000, remaining), port, lastError);
    return { url: stored.url || detailUrl, payload: stored.payload };
  } catch (e) {
    if (listSummary && listSummary.orderId) {
      const merged = mergeAmazonListSummary({ orderId: String(listSummary.orderId) }, listSummary);
      if (merged && merged.orderId) {
        return { url: detailUrl, payload: merged };
      }
    }
    throw e;
  }
}

function mergeAmazonListSummary(detailPayload, listSummary) {
  const detail =
    detailPayload && typeof detailPayload === "object" ? { ...detailPayload } : { orderId: null };
  const summary = listSummary && typeof listSummary === "object" ? listSummary : null;
  if (!summary) return detail;

  if (!detail.orderId && summary.orderId) detail.orderId = String(summary.orderId);
  if (!detail.orderDate && summary.orderDate) detail.orderDate = summary.orderDate;
  if (!detail.status && summary.status) detail.status = summary.status;
  if (!detail.detailUrl && summary.detailUrl) detail.detailUrl = summary.detailUrl;

  if ((!detail.items || detail.items.length === 0) && Array.isArray(summary.items) && summary.items.length > 0) {
    detail.items = summary.items.map((it) => ({
      asin: it && it.asin ? String(it.asin) : null,
      name: it && it.name ? String(it.name) : null,
      productUrl: it && it.productUrl ? String(it.productUrl) : null,
      quantity: 1,
      unitPrice: null,
      lineTotal: null,
    }));
  }

  if (detail.totals == null || typeof detail.totals !== "object") {
    detail.totals = { subtotal: null, grandTotal: null, orderDiscount: null };
  }
  if (detail.totals.grandTotal == null && summary.totalAmount != null) {
    detail.totals.grandTotal = summary.totalAmount;
  }

  return detail;
}

function extractAmazonOrderIdFromPayload(payload) {
  if (!payload) return "";
  if (payload.orderId) return String(payload.orderId).trim();
  return "";
}

async function sendAmazonTabMessage(tabId, message) {
  return await new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(tabId, message, (r) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          resolve({
            success: false,
            error: chrome.runtime.lastError.message || "Unknown communication error.",
          });
          return;
        }
        resolve(r || { success: false, error: "No response" });
      });
    } catch (e) {
      resolve({ success: false, error: String(e && e.message ? e.message : e) });
    }
  });
}

function tabUrlMatchesWaitHint(tabUrl, urlHint) {
  if (!urlHint) {
    return !!tabUrl && /^https:\/\/(.*\.)?amazon\.com/i.test(String(tabUrl));
  }
  if (!tabUrl) return false;
  try {
    const actual = new URL(String(tabUrl));
    const expected = new URL(String(urlHint));
    if (actual.hostname.replace(/^www\./, "") !== expected.hostname.replace(/^www\./, "")) {
      return false;
    }
    const expectedOrderId = expected.searchParams.get("orderID");
    if (expectedOrderId) {
      return actual.searchParams.get("orderID") === expectedOrderId;
    }
    return actual.pathname === expected.pathname;
  } catch {
    return String(tabUrl).split("?")[0] === String(urlHint).split("?")[0];
  }
}

async function waitForTabComplete(tabId, timeoutMs, port, options) {
  const t = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 30000;
  const opts = options && typeof options === "object" ? options : {};
  const urlHint = opts.urlHint ? String(opts.urlHint) : null;
  const requireNavigation = opts.requireNavigation === true;

  const isReadyTab = (tab) => {
    if (!tab || tab.status !== "complete") return false;
    if (urlHint) return tabUrlMatchesWaitHint(tab.url, urlHint);
    return true;
  };

  const existing = await ensureTabExists(tabId);
  if (isReadyTab(existing) && !requireNavigation) return existing;

  const urlBeforeWait = existing && existing.url ? String(existing.url) : null;

  return new Promise((resolve, reject) => {
    let settled = false;
    let heartbeatTimer = null;
    let timeoutTimer = null;
    let sawLoading = !requireNavigation;

    const noteNavigationProgress = (tab, changeInfo) => {
      if (changeInfo && changeInfo.status === "loading") sawLoading = true;
      const nextUrl = tab && tab.url ? String(tab.url) : changeInfo && changeInfo.url ? String(changeInfo.url) : null;
      if (nextUrl && urlBeforeWait && nextUrl !== urlBeforeWait) sawLoading = true;
    };

    const cleanup = () => {
      if (chrome.tabs && chrome.tabs.onUpdated) {
        try {
          chrome.tabs.onUpdated.removeListener(onUpdated);
        } catch {
          // ignore
        }
      }
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      if (timeoutTimer != null) clearTimeout(timeoutTimer);
    };

    const finishOk = (tab) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(tab);
    };

    const finishErr = (err) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(err);
    };

    function onUpdated(id, changeInfo, tab) {
      if (id !== tabId || !changeInfo) return;
      noteNavigationProgress(tab, changeInfo);
      if (changeInfo.status !== "complete") return;
      if (requireNavigation && !sawLoading) return;
      if (isReadyTab(tab)) finishOk(tab || { id: tabId, status: "complete" });
    }

    if (chrome.tabs && chrome.tabs.onUpdated) {
      chrome.tabs.onUpdated.addListener(onUpdated);
    }

    ensureTabExists(tabId).then((tab) => {
      noteNavigationProgress(tab, null);
      if (isReadyTab(tab) && (!requireNavigation || sawLoading)) finishOk(tab);
    });

    if (port) {
      heartbeatTimer = setInterval(() => {
        touchServiceWorker();
        try {
          port.postMessage({ type: "heartbeat" });
        } catch {
          // ignore
        }
      }, 4000);
    }

    timeoutTimer = setTimeout(() => {
      finishErr(new Error("Timed out waiting for tab to finish loading."));
    }, t);
  });
}

async function createAmazonScrapeTab(initialUrl, cookieStoreId) {
  return await new Promise((resolve, reject) => {
    try {
      const createProps = {
        url: initialUrl || "https://www.amazon.com/your-orders/orders",
        active: false,
      };
      if (cookieStoreId) createProps.cookieStoreId = String(cookieStoreId);
      chrome.tabs.create(createProps, (tab) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message || "Could not create Amazon tab."));
          return;
        }
        if (!tab || typeof tab.id !== "number") {
          reject(new Error("Could not create Amazon tab."));
          return;
        }
        resolve(tab.id);
      });
    } catch (e) {
      reject(e);
    }
  });
}

function broadcastToAmazonBulkPorts(message) {
  AMAZON_BULK_PORTS.forEach((port) => {
    try {
      port.postMessage(message);
    } catch {
      // ignore
    }
  });
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

async function createCostcoScrapeTab(cookieStoreId) {
  return await new Promise((resolve, reject) => {
    try {
      const createProps = { url: "https://www.costco.com/myaccount/", active: false };
      if (cookieStoreId) {
        createProps.cookieStoreId = String(cookieStoreId);
      }
      chrome.tabs.create(createProps, (tab) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message || "Could not create Costco tab."));
          return;
        }
        if (!tab || typeof tab.id !== "number") {
          reject(new Error("Could not create Costco tab."));
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

async function navigateTab(tabId, url, options) {
  const opts = options && typeof options === "object" ? options : {};
  await new Promise((resolve) => {
    try {
      const updateProps = { url };
      if (opts.active === true) updateProps.active = true;
      chrome.tabs.update(tabId, updateProps, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

async function getCostcoOrderDetailsUrlFromTab(tabId, orderHeaderId) {
  const resp = await new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(
        tabId,
        { store: "costco", type: "costcoGetOrderDetailsUrl", orderHeaderId: String(orderHeaderId) },
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
    throw new Error(resp && resp.error ? String(resp.error) : "Could not locate Costco order details URL.");
  }
  if (!resp.url) throw new Error("Missing Costco order details URL.");
  return String(resp.url);
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
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      if (!msg || msg.store !== "amazon") return;
      if (msg.type !== "amazonContentScriptReady" && msg.type !== "amazonPageReady" && msg.type !== "amazonDetailCaptured") return;
      handleAmazonContentScriptSignal(msg, sender);
    } catch {
      // ignore
    }
  });
}

if (chrome && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
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

// ---------------------------------------------------------------------------
// Single import -> bulk review bridge
// ---------------------------------------------------------------------------

if (chrome && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    try {
      if (!msg || typeof msg !== "object") return;
      if (msg.type !== "openBulkReview") return;

      (async () => {
        try {
          const orders = Array.isArray(msg.orders) ? msg.orders : [];
          if (orders.length === 0) throw new Error("At least one order payload is required.");

          const baseUrl = await getOrderManagerApiBaseUrlAsync();
          if (!baseUrl) {
            throw new Error("Order Manager base URL is not configured. Configure it in Settings first.");
          }

          const { appBase, token } = await postBulkSession(baseUrl, orders);
          const bulkUrl = appBase + "/import-review/bulk?token=" + encodeURIComponent(token);
          await openReviewTab(bulkUrl);
          try {
            sendResponse({ success: true, url: bulkUrl });
          } catch {
            // ignore
          }
        } catch (e) {
          try {
            sendResponse({
              success: false,
              error: String(e && e.message ? e.message : e),
            });
          } catch {
            // ignore
          }
        }
      })();

      // Async response
      return true;
    } catch {
      // ignore
    }
  });
}

// ---------------------------------------------------------------------------
// Costco bulk import job controller (single-page GraphQL capture)
// ---------------------------------------------------------------------------

async function getCostcoOrdersGraphqlFromTab(tabId) {
  const resp = await new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(
        tabId,
        { store: "costco", type: "costcoGetOrdersGraphql" },
        (r) => {
          if (chrome.runtime && chrome.runtime.lastError) {
            resolve({
              success: false,
              error: chrome.runtime.lastError.message || "Unknown communication error.",
            });
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
    throw new Error(resp && resp.error ? String(resp.error) : "Could not read Costco GraphQL payload.");
  }
  if (!resp.payload) {
    throw new Error("Missing Costco GraphQL payload.");
  }
  return {
    url: resp.url || null,
    payload: resp.payload,
    capturedAt: resp.capturedAt || null,
    cached: !!resp.cached,
  };
}

function extractCostcoOrderHeaderIdsFromOrdersListGraphql(payload) {
  try {
    const go = payload && payload.data && payload.data.getOnlineOrders;
    if (!Array.isArray(go) || go.length === 0) return [];
    const first = go[0] || {};
    const orders = Array.isArray(first.bcOrders) ? first.bcOrders : [];
    return orders
      .map((o) => {
        const orderNumber = o && o.orderNumber != null ? String(o.orderNumber).trim() : "";
        const orderHeaderId = o && o.orderHeaderId != null ? String(o.orderHeaderId).trim() : "";
        if (!orderNumber || !orderHeaderId) return null;
        return { orderNumber, orderHeaderId };
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

// (Costco) Order details are collected Walmart-style in a separate tab; see attachCostcoBulkPortHandlers.

function attachCostcoBulkPortHandlers(port) {
  let running = false;
  let cancelled = false;
  let scrapeTabId = null;

  port.onMessage.addListener(async (msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.store !== "costco") return;

    if (msg.type === "cancel") {
      cancelled = true;
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

    const baseUrl = await getOrderManagerApiBaseUrlAsync();
    if (!baseUrl) {
      port.postMessage({
        type: "jobError",
        error: "Order Manager base URL is not configured. Configure it in Settings first.",
      });
      return;
    }

    if (typeof msg.sourceTabId !== "number") {
      port.postMessage({ type: "jobError", error: "Missing source tab id (Costco orders tab)." });
      return;
    }

    port.postMessage({ type: "jobStarted" });

    try {
      port.postMessage({ type: "extracting" });

      const captured = await getCostcoOrdersGraphqlFromTab(msg.sourceTabId);
      if (cancelled) {
        port.postMessage({ type: "jobCancelled" });
        return;
      }

      port.postMessage({ type: "normalizing", cached: captured.cached });

      const orders = normalizeCostcoOrdersGraphqlPayloadSafe(captured.payload, captured.url);
      if (!Array.isArray(orders) || orders.length === 0) {
        port.postMessage({ type: "jobError", error: "No orders found in the captured Costco GraphQL payload." });
        return;
      }

      // Enrich orders with quantities/prices by opening order details in a NEW TAB (Walmart-style)
      // and monitoring the order-detail GraphQL response captured by the content script.
      const pairs = extractCostcoOrderHeaderIdsFromOrdersListGraphql(captured.payload);
      const detailsByOrderNumber = {};

      port.postMessage({ type: "enriching", total: pairs.length });

      // Firefox container support: open in same container as source tab.
      let cookieStoreId = null;
      try {
        const t = await ensureTabExists(msg.sourceTabId);
        if (t && t.cookieStoreId) cookieStoreId = String(t.cookieStoreId);
      } catch {
        cookieStoreId = null;
      }

      try {
        scrapeTabId = await createCostcoScrapeTab(cookieStoreId);
        port.postMessage({ type: "enrichingTabReady" });
      } catch (e) {
        port.postMessage({
          type: "enrichingTabError",
          error: String(e && e.message ? e.message : e),
        });
        scrapeTabId = null;
      }

      for (let i = 0; i < pairs.length; i++) {
        if (cancelled) {
          port.postMessage({ type: "jobCancelled" });
          return;
        }
        const p = pairs[i];
        try {
          port.postMessage({
            type: "orderDetailsStatus",
            status: "pending",
            orderNumber: p.orderNumber,
            orderHeaderId: p.orderHeaderId,
          });

          const detailUrl = await getCostcoOrderDetailsUrlFromTab(msg.sourceTabId, p.orderHeaderId);
          await clearCostcoDetailStorage();
          if (scrapeTabId != null) {
            await navigateTab(scrapeTabId, detailUrl);
          } else {
            throw new Error("Costco scrape tab was not created.");
          }
          const capturedDetail = await waitForCostcoDetail(p.orderHeaderId, 25000);
          if (capturedDetail && capturedDetail.payload) {
            detailsByOrderNumber[p.orderNumber] = capturedDetail.payload;
          }
          port.postMessage({
            type: "orderDetailsStatus",
            status: "ok",
            orderNumber: p.orderNumber,
            orderHeaderId: p.orderHeaderId,
          });
          await sleep(400);
        } catch (e) {
          port.postMessage({
            type: "orderDetailsStatus",
            status: "error",
            orderNumber: p.orderNumber,
            orderHeaderId: p.orderHeaderId,
            error: String(e && e.message ? e.message : e),
          });
        }
      }

      try {
        const c = globalThis.OrderManagerCostco;
        if (c && typeof c.mergeCostcoOrderDetailsIntoNormalizedOrders === "function") {
          c.mergeCostcoOrderDetailsIntoNormalizedOrders(orders, detailsByOrderNumber);
        }
      } catch {
        // ignore
      }

      port.postMessage({ type: "creatingSession", total: orders.length });

      const { appBase, token } = await postBulkSession(baseUrl, orders);
      const bulkUrl = appBase + "/import-review/bulk?token=" + encodeURIComponent(token);
      await openReviewTab(bulkUrl);
      port.postMessage({ type: "reviewReady", url: bulkUrl });

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
      if (!port || port.name !== "costcoBulkImport") return;
      try {
        COSTCO_BULK_PORTS.add(port);
      } catch {
        // ignore
      }
      try {
        port.onDisconnect.addListener(() => {
          try {
            COSTCO_BULK_PORTS.delete(port);
          } catch {
            // ignore
          }
        });
      } catch {
        // ignore
      }
      attachCostcoBulkPortHandlers(port);
    } catch {
      // ignore
    }
  });
}

// ---------------------------------------------------------------------------
// Amazon bulk import job controller (page-at-a-time)
// ---------------------------------------------------------------------------

function attachAmazonBulkPortHandlers(port) {
  let running = false;
  let cancelled = false;
  let scrapeTabId = null;

  port.onMessage.addListener(async (msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.store !== "amazon") return;

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

    const maxPagesRaw =
      typeof msg.maxPages === "number" ? msg.maxPages : parseInt(String(msg.maxPages || "1"), 10);
    const maxPages = Number.isFinite(maxPagesRaw) && maxPagesRaw > 0 ? Math.min(Math.floor(maxPagesRaw), 50) : 1;

    const baseUrl = await getOrderManagerApiBaseUrlAsync();
    if (!baseUrl) {
      port.postMessage({
        type: "jobError",
        error: "Order Manager base URL is not configured. Configure it in Settings first.",
      });
      return;
    }

    if (typeof msg.sourceTabId !== "number") {
      port.postMessage({ type: "jobError", error: "Missing source tab id (Amazon order history tab)." });
      return;
    }

    const sourceTab = await ensureTabExists(msg.sourceTabId);
    if (!sourceTab) {
      port.postMessage({ type: "jobError", error: "Amazon order history tab is no longer available." });
      return;
    }

    let cookieStoreId = sourceTab.cookieStoreId ? String(sourceTab.cookieStoreId) : null;

    port.postMessage({ type: "jobStarted" });

    try {
      await sendAmazonTabMessage(msg.sourceTabId, { store: "amazon", type: "amazonFetchAccountEmail" });
      let accountEmail = await getAmazonAccountEmailAsync();

      const sourceOrigin = (() => {
        try {
          if (sourceTab.url) {
            const am = globalThis.OrderManagerAmazon;
            if (am && typeof am.originFromUrl === "function") return am.originFromUrl(sourceTab.url);
            return new URL(sourceTab.url).origin;
          }
        } catch {
          // ignore
        }
        return "https://www.amazon.com";
      })();

      await ensureAmazonCsdDisabledCookie(sourceOrigin, cookieStoreId);

      const collectedPayloads = [];
      let ordersDone = 0;
      let ordersOk = 0;
      let ordersErr = 0;
      const detailTabId = msg.sourceTabId;

      for (let pageIndex = 0; pageIndex < maxPages; pageIndex++) {
        if (cancelled) {
          port.postMessage({ type: "jobCancelled" });
          return;
        }

        port.postMessage({
          type: "pageStatus",
          status: "pending",
          page: pageIndex + 1,
          maxPages,
        });

        const listReady = await waitForAmazonContentScript(msg.sourceTabId, 15000, port);
        if (!listReady) {
          throw new Error("Amazon order list page is not ready.");
        }

        const listPageReady = await waitForAmazonTabSignal(msg.sourceTabId, "list", 30000, port);

        const listResp = await sendAmazonTabMessage(msg.sourceTabId, {
          store: "amazon",
          type: "amazonParseCurrentListPage",
          pageAlreadyReady: listPageReady,
        });

        if (!listResp || listResp.success !== true) {
          throw new Error(
            listResp && listResp.error
              ? String(listResp.error)
              : "Could not parse Amazon order list page."
          );
        }

        const pageOrders = Array.isArray(listResp.orders) ? listResp.orders : [];
        port.postMessage({
          type: "pageOrdersReady",
          page: pageIndex + 1,
          maxPages,
          count: pageOrders.length,
        });

        const nextResp = await sendAmazonTabMessage(msg.sourceTabId, {
          store: "amazon",
          type: "amazonGetNextPageUrl",
        });
        const nextListPageUrl =
          nextResp && nextResp.success === true && nextResp.url ? String(nextResp.url) : null;

        for (let oi = 0; oi < pageOrders.length; oi++) {
          if (cancelled) {
            port.postMessage({ type: "jobCancelled" });
            return;
          }

          const summary = pageOrders[oi] || {};
          const orderId = summary.orderId ? String(summary.orderId) : "";
          const detailUrl = summary.detailUrl ? String(summary.detailUrl) : "";
          if (!orderId || !detailUrl) continue;

          ordersDone++;
          port.postMessage({
            type: "orderStatus",
            status: "pending",
            orderNumber: orderId,
            page: pageIndex + 1,
            maxPages,
          });

          try {
            await clearAmazonDetailStorage();
            await ensureAmazonCsdDisabledCookie(sourceOrigin, cookieStoreId);

            const listScriptReady = await waitForAmazonContentScript(detailTabId, 15000, port);
            if (!listScriptReady) {
              throw new Error("Amazon content script is not reachable on the order list tab.");
            }

            const detailMsg = await sendAmazonTabMessage(detailTabId, {
              store: "amazon",
              type: "amazonCaptureOrderDetailFromUrl",
              detailUrl,
            });

            let payload = null;
            if (detailMsg && detailMsg.success === true && detailMsg.order && detailMsg.order.orderId) {
              payload = detailMsg.order;
            } else if (summary && summary.orderId) {
              const merged = mergeAmazonListSummary({ orderId: String(summary.orderId) }, summary);
              if (merged && merged.orderId) payload = merged;
            }

            if (!payload || !payload.orderId) {
              throw new Error(
                detailMsg && detailMsg.error
                  ? String(detailMsg.error)
                  : "Could not capture Amazon order detail."
              );
            }

            if (!accountEmail) accountEmail = await getAmazonAccountEmailAsync();
            const body = normalizeAmazonOrderPayloadSafe(
              payload,
              detailUrl,
              accountEmail
            );
            collectedPayloads.push(body);
            ordersOk++;
            port.postMessage({ type: "orderStatus", status: "ok", orderNumber: orderId });
          } catch (e) {
            ordersErr++;
            port.postMessage({
              type: "orderStatus",
              status: "error",
              orderNumber: orderId,
              error: String(e && e.message ? e.message : e),
            });
          }

          port.postMessage({
            type: "counts",
            total: ordersDone,
            done: ordersDone,
            ok: ordersOk,
            err: ordersErr,
          });

          if (oi < pageOrders.length - 1) await keepaliveSleep(400, port);
        }

        if (pageIndex + 1 >= maxPages) break;

        if (!nextListPageUrl) {
          port.postMessage({ type: "pageStatus", status: "done", page: pageIndex + 1, maxPages, noMore: true });
          break;
        }

        await navigateTab(msg.sourceTabId, nextListPageUrl);
        await waitForTabComplete(msg.sourceTabId, 35000, port, {
          urlHint: nextListPageUrl,
          requireNavigation: true,
        });
        await keepaliveSleep(400, port);
        port.postMessage({ type: "pageStatus", status: "ok", page: pageIndex + 1, maxPages });
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
      if (!port || port.name !== "amazonBulkImport") return;
      try {
        AMAZON_BULK_PORTS.add(port);
      } catch {
        // ignore
      }
      try {
        port.onDisconnect.addListener(() => {
          try {
            AMAZON_BULK_PORTS.delete(port);
          } catch {
            // ignore
          }
        });
      } catch {
        // ignore
      }
      attachAmazonBulkPortHandlers(port);
    } catch {
      // ignore
    }
  });
}
