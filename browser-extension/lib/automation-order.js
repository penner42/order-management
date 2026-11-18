;(function () {
  "use strict";

  function validOrderNumber(value) {
    const number = String(value ?? "").trim();
    return number.length <= 64 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/i.test(number) ? number : null;
  }

  function orderNumberFromPage(retailer, pageUrl) {
    try {
      const url = new URL(pageUrl);
      if (retailer === "amazon") return globalThis.OrderManagerAmazon.orderIdFromUrl(pageUrl);
      if (retailer === "costco") {
        const match = /^#\/app\/[^/]+\/orderdetails\/([^/?#]+)(?:[/?#]|$)/i.exec(url.hash);
        return match ? validOrderNumber(decodeURIComponent(match[1])) : null;
      }
      if (retailer === "walmart") {
        const match = /^\/orders\/([^/]+)\/?$/.exec(url.pathname);
        const segment = match && decodeURIComponent(match[1]);
        if (segment && segment.toLowerCase() !== "order-details") return validOrderNumber(segment);
        for (const [key, value] of url.searchParams) {
          if (/^order(?:id|number)$/i.test(key)) return validOrderNumber(value);
        }
      }
    } catch {
      return null;
    }
    return null;
  }

  function resolveOrderContext(retailer, pageUrl, pageData) {
    const fromUrl = orderNumberFromPage(retailer, pageUrl);
    const fromTab = pageData?.url === pageUrl ? validOrderNumber(pageData.orderNumber) : null;
    return {
      orderNumber: fromUrl || fromTab,
      email: fromTab && (!fromUrl || fromUrl === fromTab) ? pageData.email : null,
    };
  }

  function suggestedProfile(profiles, email) {
    if (profiles.length === 1) return profiles[0];
    const normalized = typeof email === "string" ? email.trim().toLowerCase() : "";
    if (!normalized) return null;
    const matches = profiles.filter((profile) =>
      profile.store_account_name?.trim().toLowerCase() === normalized
    );
    return matches.length === 1 ? matches[0] : null;
  }

  function appBaseUrl(baseUrl) {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("Check the Order Manager server URL in extension settings.");
    }
    url.hash = "";
    url.search = "";
    return url.href.replace(/\/+$/, "").replace(/\/api$/, "");
  }

  async function request(appBase, token, path, options) {
    const response = await fetch(appBase + "/api/browser-profiles" + path, {
      ...options,
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      redirect: "error",
    });
    if (response.status === 401) {
      throw new Error("Reconnect Order Management in the extension, then try again.");
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = Array.isArray(data?.detail) ? data.detail[0]?.msg : data?.detail;
      throw new Error(typeof detail === "string" ? detail : "Order Manager returned HTTP " + response.status + ".");
    }
    return data;
  }

  globalThis.OrderManagerAutomationOrder = { resolveOrderContext, suggestedProfile, appBaseUrl, request };
})();
