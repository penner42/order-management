const ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY = "orderManagerExtensionToken";
const EXT_TOKEN_HASH_PREFIX = "#ext-token=";

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  try {
    const url = new URL(changeInfo.url);
    if (!url.hash || !url.hash.startsWith(EXT_TOKEN_HASH_PREFIX)) return;
    if (!url.pathname.includes("/extension-auth")) return;

    const token = decodeURIComponent(url.hash.slice(EXT_TOKEN_HASH_PREFIX.length));
    if (!token) return;

    chrome.storage.local.set({ [ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY]: token }, () => {
      try {
        chrome.tabs.remove(tabId);
      } catch {
        // best-effort close
      }
    });
  } catch {
    // ignore malformed URLs
  }
});
