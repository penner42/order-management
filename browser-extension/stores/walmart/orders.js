/**
 * Order Manager Browser Integration - Walmart Orders
 * All data is read from the __NEXT_DATA__ script JSON (no DOM parsing).
 * - Order detail page (orders/XXXX/...): order and line items from props.pageProps.initialData.data.order
 * - Orders list page (orders): order numbers from __NEXT_DATA__ when present
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

  function getOrderNumbersFromNextData() {
    const data = getNextData();
    const initialData = data?.props?.pageProps?.phRedesignInitialData;
    const dataObj = initialData?.data;
    if (!dataObj) return [];

    const orders = dataObj.purchaseHistory?.orders

    if (!orders || !Array.isArray(orders)) return [];
    const numbers = new Array();
    for (const o of orders) {
      const id = String(o.id);
      if (id != null && !numbers.includes(id)) numbers.push(id);
    }
    return numbers; //Array.from(numbers).sort();
  }

  const ORDER_LABEL_MARKER = "data-order-manager-labeled";

  function injectOrderNumbersIntoPage() {
    const orderNumbers = getOrderNumbersFromNextData();
    if (orderNumbers.length === 0) return false;
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
      header.setAttribute(ORDER_LABEL_MARKER, "1");
      injected++;
    }
    return injected > 0;
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

  if (/^\/orders(\/|$)/.test(window.location.pathname || "")) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", runWhenOrdersPageReady);
    } else {
      runWhenOrdersPageReady();
    }
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
