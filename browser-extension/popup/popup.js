/** Order details from the content script (getOrderDetails). Stored after a successful fetch so "Send to Order Manager" can use it. */
let lastOrderDetails = null;

const WALMART_ORDER_DETAIL_STORAGE_KEY = "walmartOrderDetail";
const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY = "orderManagerApiBaseUrl";
const ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY = "orderManagerExtensionToken";

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

function getOrderManagerApiBaseUrl(callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    callback(null);
    return;
  }
  try {
    chrome.storage.local.get(ORDER_MANAGER_API_BASE_URL_STORAGE_KEY, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback(null);
        return;
      }
      const value =
        data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY]).trim()
          : "";
      callback(value || null);
    });
  } catch {
    callback(null);
  }
}

function getOrderManagerAuthToken(callback) {
  if (!chrome || !chrome.storage || !chrome.storage.local) {
    callback(null);
    return;
  }
  try {
    chrome.storage.local.get(ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback(null);
        return;
      }
      const value =
        data && data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY]).trim()
          : "";
      callback(value || null);
    });
  } catch {
    callback(null);
  }
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

  disconnectBtn.addEventListener("click", () => {
    try {
      chrome.storage.local.remove(ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY, () => {
        showLoggedOut();
      });
    } catch {
      showLoggedOut();
    }
  });

  connectBtn.addEventListener("click", () => {
    statusEl.textContent = "Opening authorization…";
    getOrderManagerApiBaseUrl((baseUrl) => {
      if (!baseUrl) {
        statusEl.innerHTML =
          'Order Manager base URL is not configured. Use <span style="font-weight:600;">Settings</span> to set it first.';
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
            getOrderManagerAuthToken((token) => {
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
})();

function normalizeWalmartOrderDetailPayload(payload, sourceUrl) {
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
    rawPayload: raw || null,
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
          resultsEl.textContent = "Sending to Order Manager…";

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
                    '<div class="error">Order Manager API base URL is not configured.</div>' +
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

                let endpoint = baseUrl;
                if (endpoint.endsWith("/")) {
                  endpoint = endpoint.slice(0, -1);
                }
                endpoint += "/api/integrations/stores/orders/import";

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

                getOrderManagerAuthToken((authToken) => {
                  const headers = {
                    "Content-Type": "application/json",
                  };
                  if (authToken) {
                    headers["Authorization"] = "Bearer " + authToken;
                  }

                  fetch(endpoint, {
                    method: "POST",
                    headers: headers,
                    body: JSON.stringify(body),
                  })
                    .then((response) => {
                      if (!response.ok) {
                        return response
                          .text()
                          .catch(() => "")
                          .then((text) => {
                            const msg =
                              "Request failed: " +
                              response.status +
                              " " +
                              response.statusText +
                              (text ? " - " + text : "");
                            throw new Error(msg);
                          });
                      }
                      return response.json().catch(() => null);
                    })
                    .then((data) => {
                      resultsEl.innerHTML =
                        '<span>Sent to Order Manager for review.</span>';
                      if (data && data.id) {
                        let detailUrl = baseUrl;
                        if (detailUrl.endsWith('/')) detailUrl = detailUrl.slice(0, -1);
                        detailUrl += '/store-imports/' + data.id;
                        chrome.tabs.create({ url: detailUrl });
                      }
                    })
                    .catch((err) => {
                      resultsEl.innerHTML =
                        '<span class="error">Error sending to Order Manager: ' +
                        escapeHtml(
                          String(err && err.message ? err.message : err)
                        ) +
                        "</span>";
                    });
                });
              });
            }
          );
        });
      }
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
