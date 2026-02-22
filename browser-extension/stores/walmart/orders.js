/**
 * Order Manager Browser Integration - Walmart Orders
 * - Order detail page (orders/XXXX/...): order and line items from __NEXT_DATA__ (props.pageProps.initialData.data.order)
 * - Orders list page (orders): order numbers from buttons with data-automation-id="view-order-details-link-XX" (XX = order number)
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

  const VIEW_ORDER_DETAILS_PREFIX = "view-order-details-link-";

  function getOrderNumbersFromButtons() {
    const selector = `[data-automation-id^="${VIEW_ORDER_DETAILS_PREFIX}"]`;
    const buttons = document.querySelectorAll(selector);
    const numbers = new Set();
    for (const el of buttons) {
      const id = el.getAttribute("data-automation-id");
      if (!id || !id.startsWith(VIEW_ORDER_DETAILS_PREFIX)) continue;
      const orderNumber = id.slice(VIEW_ORDER_DETAILS_PREFIX.length).trim();
      if (orderNumber) numbers.add(orderNumber);
    }
    return Array.from(numbers).sort();
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
    if (request.action === "getOrderNumbers") {
      const orderNumbers = getOrderNumbersFromButtons();
      const message =
        orderNumbers.length === 0
          ? "Order Manager: No order numbers found (look for view-order-details-link buttons on this page)."
          : `Order Manager found ${orderNumbers.length} order(s):\n\n` +
            orderNumbers.join("\n");
      alert(message);
      sendResponse({ orderNumbers });
      return true;
    }
    return false;
  });
})();
