/**
 * Order Manager Browser Integration - Costco Orders
 * On ordersandpurchases page: injects "Tracking # YYY" after the nearest preceding
 * MuiTypography-t4 div for each Track My Package link (aria-describedby="TrackMyPackageBtn_YYY").
 */
(function () {
  "use strict";

  const TRACK_BTN_PREFIX = "TrackMyPackageBtn_";
  const D_SELECTOR = "div.MuiTypography-root.MuiTypography-t4.css-18dryg3";
  const INJECTED_MARKER = "data-order-manager-tracking";

  function isOrdersAndPurchasesPage() {
    return /#\/app\/[^/]+\/ordersandpurchases/.test(
      window.location.hash || ""
    );
  }

  /**
   * Find the immediately preceding div matching D (MuiTypography-t4) before `anchor`
   * in document order — i.e. the last such div that appears before the anchor.
   */
  function findNearestPrecedingD(anchor) {
    const root = document.body;
    if (!root || !root.contains(anchor)) return null;

    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (node) {
        if (node === anchor) return NodeFilter.FILTER_REJECT;
        if (
          node !== anchor &&
          node.tagName === "DIV" &&
          node.classList.contains("MuiTypography-root") &&
          node.classList.contains("MuiTypography-t4") &&
          node.classList.contains("css-18dryg3")
        )
          return NodeFilter.FILTER_ACCEPT;
        return NodeFilter.FILTER_SKIP;
      },
    });
    tw.currentNode = anchor;
    return tw.previousNode();
  }

  function injectTrackingAfterD(trackingNumber) {
    const outer = document.createElement("div");
    outer.className = "MuiTypography-root MuiTypography-t5 css-ifsdji";
    outer.setAttribute(INJECTED_MARKER, "true");
    const middle = document.createElement("div");
    middle.className = "MuiBox-root css-1w1vbxm";
    middle.appendChild(document.createTextNode("Tracking #"));

    const inner = document.createElement("div");
    inner.className = "MuiTypography-root MuiTypography-t5 css-qpcy40";
    inner.textContent = trackingNumber;

    middle.appendChild(inner);
    outer.appendChild(middle);
    return outer;
  }

  function run() {
    if (!isOrdersAndPurchasesPage()) return;

    const anchors = document.querySelectorAll(
      `a[aria-describedby^="${TRACK_BTN_PREFIX}"]`
    );

    for (const a of anchors) {
      const describedBy = a.getAttribute("aria-describedby") || "";
      const trackingNumber = describedBy.slice(TRACK_BTN_PREFIX.length).trim();
      if (!trackingNumber) continue;

      const d = findNearestPrecedingD(a);
      if (!d) continue;

      // Find last consecutive injected sibling after D so we append after any existing blocks.
      let insertAfter = d;
      while (
        insertAfter.nextElementSibling?.getAttribute(INJECTED_MARKER) === "true"
      ) {
        insertAfter = insertAfter.nextElementSibling;
      }
      // Skip if we already injected this tracking number after this D.
      let el = d.nextElementSibling;
      let alreadyInjected = false;
      while (el && el.getAttribute(INJECTED_MARKER) === "true") {
        if (el.textContent.includes(trackingNumber)) {
          alreadyInjected = true;
          break;
        }
        el = el.nextElementSibling;
      }
      if (alreadyInjected) continue;

      const injected = injectTrackingAfterD(trackingNumber);
      insertAfter.insertAdjacentElement("afterend", injected);
      if (injected.parentElement) {
        injected.parentElement.className = "MuiBox-root css-1hvnmkt";
      }
    }
  }

  run();
  // Re-run when hash or DOM changes (SPA navigation / dynamic content).
  window.addEventListener("hashchange", run);
  let runTimeout = null;
  const observer = new MutationObserver(function () {
    if (runTimeout) clearTimeout(runTimeout);
    runTimeout = setTimeout(run, 150);
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
