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

  function waitForPageLoad(beforeOrders, maxWaitMs = 8000) {
    return new Promise((resolve) => {
      const start = Date.now();

      const check = () => {
        const current = extractOrderNumbers();
        const hasNew = current.some((o) => !beforeOrders.has(o));
        if (hasNew || Date.now() - start > maxWaitMs) {
          resolve();
          return;
        }
        setTimeout(check, 300);
      };
      setTimeout(check, 500);
    });
  }

  async function getOrderNumbersFromPages(pageCount) {
    const allOrderNumbers = new Set();

    for (let page = 0; page < pageCount; page++) {
      const orders = extractOrderNumbers();
      orders.forEach((o) => allOrderNumbers.add(o));

      if (page < pageCount - 1) {
        const nextBtn = document.querySelector('[aria-label="Next page"]');
        if (!nextBtn) break;

        const beforeOrders = new Set(extractOrderNumbers());
        nextBtn.click();
        await waitForPageLoad(beforeOrders);
      }
    }

    return Array.from(allOrderNumbers).sort();
  }

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === "getOrderNumbers") {
      const pageCount = Math.max(1, request.pageCount || 1);

      getOrderNumbersFromPages(pageCount).then((orderNumbers) => {
        const message =
          orderNumbers.length === 0
            ? "Order Manager: No order numbers found on this page."
            : `Order Manager found ${orderNumbers.length} order(s):\n\n` +
              orderNumbers.join("\n");
        alert(message);
        sendResponse({ orderNumbers });
      });

      return true;
    }
    return false;
  });
})();
