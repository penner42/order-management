/**
 * Order Manager Browser Integration - Walmart Orders
 * All data is read from the __NEXT_DATA__ script JSON (no DOM parsing).
 * - Order detail page (orders/XXXX/...): order and line items from props.pageProps.initialData.data.order
 * - Orders list page (orders): order numbers and tracking numbers (orders[].groups[].shipment.trackingNumber) from __NEXT_DATA__ and fetch interceptor; injected into list and appended to track-package button.
 */
(function () {
  "use strict";

  function isOrderDetailPage() {
    const match = /^\/orders\/([^/]+)(?:\/|$)/.exec(
      window.location.pathname || ""
    );
    return match ? { orderNumber: match[1] } : null;
  }

  function getNextData() {
    const script = document.getElementById("__NEXT_DATA__");
    if (!script || !script.textContent) return null;
    try {
      return JSON.parse(script.textContent);
    } catch {
      return null;
    }
  }

  function getOrderFromNextData() {
    const data = getNextData();
    return data?.props?.pageProps?.initialData?.data?.order ?? null;
  }

  function extractOrderDetailsFromPage() {
    const pageInfo = isOrderDetailPage();
    if (!pageInfo) return null;

    const nextOrder = getOrderFromNextData();
    if (!nextOrder) return null;

    const orderId =
      nextOrder.id != null ? String(nextOrder.id) : pageInfo.orderNumber;
    const orderNumber =
      nextOrder.id != null ? String(nextOrder.id) : pageInfo.orderNumber;
    const account = nextOrder.customer?.email ?? "";
    const orderDate = nextOrder.orderDate ?? "";

    const rawItems = [];
    const deliveryAddresses = [];
    for (const key of Object.keys(nextOrder)) {
      if (!key.startsWith("groups_")) continue;
      const groupList = nextOrder[key];
      if (!Array.isArray(groupList)) continue;
      for (const group of groupList) {
        const da = group.deliveryAddress;
        if (da) {
          deliveryAddresses.push({
            fullName: da.fullName ?? "",
            addressString: da.address?.addressString ?? "",
          });
        }
        const trackingNumber = group.shipment?.trackingNumber ?? "";
        if (!group.items || !Array.isArray(group.items)) continue;
        for (const it of group.items) {
          const priceInfo = it.priceInfo;
          const qty = it.quantity ?? it.qty ?? 0;
          // Prefer unit cost so quantity × unit = line total; fall back to line total ÷ quantity when only line price exists
          let price =
            priceInfo?.unitPrice?.displayValue ??
            (priceInfo?.unitPrice?.value != null
              ? String(priceInfo.unitPrice.value)
              : "");
          if (!price && priceInfo?.linePrice?.value != null && qty > 0) {
            price = String(priceInfo.linePrice.value / qty);
          }
          if (!price) {
            price =
              priceInfo?.linePrice?.displayValue ??
              (priceInfo?.linePrice?.value != null
                ? String(priceInfo.linePrice.value)
                : "") ??
              "";
          }
          rawItems.push({
            name: it.productInfo?.name ?? "",
            quantity: qty,
            price,
            trackingNumber,
          });
        }
      }
    }

    const address = deliveryAddresses
      .map((da) => (da.fullName ? `${da.fullName}\n${da.addressString}` : da.addressString))
      .filter(Boolean)
      .join("\n\n");

    return {
      orderNumber,
      id: orderId,
      items: rawItems,
      account,
      orderDate,
      deliveryAddresses,
      address,
      paymentMethod: "",
    };
  }

  function extractOrderNumbersFromDataObj(dataObj) {
    if (!dataObj) return [];
    const orders = dataObj.purchaseHistory?.orders;
    if (!orders || !Array.isArray(orders)) return [];
    const numbers = [];
    for (const o of orders) {
      const id = String(o.id);
      if (id != null && !numbers.includes(id)) numbers.push(id);
    }
    return numbers;
  }

  function extractTrackingNumbersFromDataObj(dataObj) {
    if (!dataObj) return [];
    const orders = dataObj.purchaseHistory?.orders;
    if (!orders || !Array.isArray(orders)) return [];
    const byIndex = [];
    for (const o of orders) {
      const groups = o.groups;
      const tracking = [];
      if (Array.isArray(groups)) {
        for (const g of groups) {
          const tn = g.shipment?.trackingNumber;
          if (tn != null && String(tn).trim()) {
            tracking.push(String(tn).trim());
          }
        }
      }
      byIndex.push(tracking);
    }
    return byIndex;
  }

  function getOrderNumbersFromNextData() {
    const data = getNextData();
    const initialData = data?.props?.pageProps?.phRedesignInitialData;
    const dataObj = initialData?.data;
    return extractOrderNumbersFromDataObj(dataObj);
  }

  function getTrackingNumbersFromNextData() {
    const data = getNextData();
    const initialData = data?.props?.pageProps?.phRedesignInitialData;
    const dataObj = initialData?.data;
    return extractTrackingNumbersFromDataObj(dataObj);
  }

  const ORDER_LABEL_MARKER = "data-order-manager-labeled";

  function injectOrderNumbersWithArray(orderNumbers, trackingNumbersByIndex) {
    if (!orderNumbers || orderNumbers.length === 0) return false;
    let injected = 0;
    for (let X = 0; X < orderNumbers.length; X++) {
      const orderEl = document.querySelector(
        `div[data-testid="order-${X}"]`
      );
      if (!orderEl) continue;
      const header = orderEl.querySelector(
        'div.pa3.ph4-m.flex.items-center.justify-between.bg-nearer-white'
      );
      if (!header || header.hasAttribute(ORDER_LABEL_MARKER)) continue;
      const Y = orderNumbers[X];
      const span = document.createElement("span");
      span.className = "w_yTSq dark-gray w_0aYG w_TErl";
      span.textContent = `Order number ${Y}`;
      header.insertBefore(span, header.firstChild);
      const trackingForOrder = Array.isArray(trackingNumbersByIndex) && trackingNumbersByIndex[X];
      const trackingList = Array.isArray(trackingForOrder)
        ? trackingForOrder.filter(Boolean)
        : [];
      const orderCard = orderEl.parentElement || orderEl;
      for (let g = 0; g < trackingList.length; g++) {
        const tn = trackingList[g];
        if (!tn) continue;
        const groupEl = orderCard.querySelector(
          `div[data-testid="orderGroup-${g}"]`
        );
        if (!groupEl) continue;
        const captionId = "caption-" + orderNumbers[X] + "-Delivery";
        const captionEl = groupEl.querySelector(
          "[id='" + captionId.replace(/'/g, "\\'") + "']"
        );
        if (captionEl) {
          captionEl.textContent = (captionEl.textContent || "").trim() + " " + tn;
        }
      }
      header.setAttribute(ORDER_LABEL_MARKER, "1");
      injected++;
    }
    return injected > 0;
  }

  function injectOrderNumbersIntoPage() {
    const orderNumbers = getOrderNumbersFromNextData();
    const trackingNumbersByIndex = getTrackingNumbersFromNextData();
    return injectOrderNumbersWithArray(orderNumbers, trackingNumbersByIndex);
  }

  function scheduleInjectFromPayload(orderNumbers, trackingNumbersByIndex) {
    const tryInject = (attempt) => {
      if (injectOrderNumbersWithArray(orderNumbers, trackingNumbersByIndex)) return;
      if (attempt < 15) setTimeout(() => tryInject(attempt + 1), 300);
    };
    setTimeout(() => tryInject(0), 100);
  }

  function runWhenOrdersPageReady() {
    if (injectOrderNumbersIntoPage()) return;
    let attempts = 0;
    const maxAttempts = 20;
    const interval = setInterval(() => {
      attempts++;
      if (injectOrderNumbersIntoPage() || attempts >= maxAttempts) {
        clearInterval(interval);
      }
    }, 500);
  }

  function isOrdersPageFetch(urlString) {
    if (!urlString || typeof urlString !== "string") return false;
    try {
      const u = new URL(urlString, window.location.origin);
      return /\/orchestra\/cph\/graphql\/PurchaseHistory/.test(u.pathname);
    } catch {
      return false;
    }
  }

  if (/^\/orders(\/|$)/.test(window.location.pathname || "")) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", runWhenOrdersPageReady);
    } else {
      runWhenOrdersPageReady();
    }

    const originalFetch = window.fetch;
    window.fetch = function fetchInterceptor(...args) {
      const requestUrl =
        typeof args[0] === "string" ? args[0] : args[0]?.url;
      const isOrdersPagination = isOrdersPageFetch(requestUrl);

      return originalFetch.apply(this, args).then(async (response) => {
        if (!isOrdersPagination) return response;
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) return response;
        try {
          const clone = response.clone();
          const body = await clone.json();
          const dataObj =
            body?.data ??
            body?.props?.pageProps?.phRedesignInitialData?.data ??
            body;
          const orderNumbers = extractOrderNumbersFromDataObj(dataObj);
          const trackingNumbersByIndex = extractTrackingNumbersFromDataObj(dataObj);
          if (orderNumbers.length > 0) {
            scheduleInjectFromPayload(orderNumbers, trackingNumbersByIndex);
          }
        } catch {
          // ignore
        }
        return response;
      });
    };
  }

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === "getOrderDetails") {
      const details = extractOrderDetailsFromPage();
      if (!details) {
        sendResponse({ error: "not_order_detail_page" });
      } else {
        sendResponse({ details });
      }
      return true;
    }
    return false;
  });
})();
