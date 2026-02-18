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
          const price =
            priceInfo?.linePrice?.displayValue ??
            priceInfo?.unitPrice?.displayValue ??
            (priceInfo?.linePrice?.value != null
              ? String(priceInfo.linePrice.value)
              : "") ??
            "";
          rawItems.push({
            name: it.productInfo?.name ?? "",
            quantity: it.quantity ?? it.qty ?? 0,
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
    const initialData = data?.props?.pageProps?.initialData;
    const dataObj = initialData?.data;
    if (!dataObj) return [];

    const orders =
      dataObj.orders ??
      dataObj.orderList ??
      (Array.isArray(dataObj) ? dataObj : null);
    if (!orders || !Array.isArray(orders)) return [];

    const numbers = new Set();
    for (const o of orders) {
      const id = o.displayId ?? o.orderNumber ?? o.id;
      if (id != null) numbers.add(String(id));
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
      const orderNumbers = getOrderNumbersFromNextData();
      const message =
        orderNumbers.length === 0
          ? "Order Manager: No order numbers found in __NEXT_DATA__ on this page."
          : `Order Manager found ${orderNumbers.length} order(s):\n\n` +
            orderNumbers.join("\n");
      alert(message);
      sendResponse({ orderNumbers });
      return true;
    }
    return false;
  });
})();
