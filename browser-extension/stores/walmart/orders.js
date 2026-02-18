/**
 * Order Manager Browser Integration - Walmart Orders
 * Reads order numbers from the Walmart orders page and displays them.
 * Walmart uses data-automation-id="view-order-details-link-XXXX" where XXXX is the order number.
 */
(function () {
  "use strict";

  const PREFIX = "view-order-details-link-";

  function extractOrderNumbers() {
    const links = document.querySelectorAll(
      `[data-automation-id^="${PREFIX}"]`
    );
    const orderNumbers = new Set();

    for (const link of links) {
      const id = link.getAttribute("data-automation-id");
      if (id && id.startsWith(PREFIX)) {
        const orderNumber = id.slice(PREFIX.length);
        if (orderNumber) {
          orderNumbers.add(orderNumber);
        }
      }
    }

    return Array.from(orderNumbers).sort();
  }

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === "getOrderNumbers") {
      const orderNumbers = extractOrderNumbers();
      const message =
        orderNumbers.length === 0
          ? "Order Manager: No order numbers found on this page."
          : `Order Manager found ${orderNumbers.length} order(s):\n\n` +
            orderNumbers.join("\n");
      alert(message);
      sendResponse({ orderNumbers });
    }
    return true;
  });
})();
