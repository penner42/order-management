const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY = "orderManagerApiBaseUrl";
const ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY = "orderManagerProdApiBaseUrl";
const ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY = "orderManagerDevApiBaseUrl";
const ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY = "orderManagerDevEnabled";

const ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY = "orderManagerExtensionToken";
const ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY = "orderManagerExtensionTokenProd";
const ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY = "orderManagerExtensionTokenDev";

function setStatus(el, message, kind) {
  if (!el) return;
  el.textContent = message || "";
  el.classList.remove("ok", "error");
  if (kind === "ok") el.classList.add("ok");
  if (kind === "error") el.classList.add("error");
}

function setHint(el, message) {
  if (!el) return;
  el.textContent = message || "";
}

function normalizeBaseUrl(value) {
  const v = String(value || "").trim();
  if (!v) return "";

  let url;
  try {
    url = new URL(v);
  } catch {
    throw new Error("Please enter a valid URL (including http:// or https://).");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("URL must start with http:// or https://");
  }

  // Store a stable representation, without a trailing slash.
  let normalized = url.toString();
  if (normalized.endsWith("/")) normalized = normalized.slice(0, -1);
  return normalized;
}

function normalizeServer(server) {
  return server === "dev" ? "dev" : "prod";
}

function getAuthTokenKey(server) {
  return normalizeServer(server) === "dev"
    ? ORDER_MANAGER_EXTENSION_TOKEN_DEV_STORAGE_KEY
    : ORDER_MANAGER_EXTENSION_TOKEN_PROD_STORAGE_KEY;
}

function getServerBaseUrlFromStorage(server, callback) {
  const s = normalizeServer(server);
  const keys =
    s === "dev"
      ? [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY, ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
      : [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY, ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY];

  try {
    chrome.storage.local.get(keys, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback({ enabled: false, baseUrl: "" });
        return;
      }

      if (s === "dev") {
        const enabled = !!(data && data[ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]);
        const baseUrl =
          data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
            ? String(data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]).trim()
            : "";
        callback({ enabled, baseUrl });
        return;
      }

      const baseUrl =
        data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]).trim()
          : data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]
            ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]).trim()
            : "";
      callback({ enabled: true, baseUrl });
    });
  } catch {
    callback({ enabled: false, baseUrl: "" });
  }
}

function getAuthToken(server, callback) {
  const s = normalizeServer(server);
  const key = getAuthTokenKey(s);
  const keys = [key];
  // Back-compat: prod can fall back to legacy token key.
  if (s === "prod") keys.push(ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY);

  try {
    chrome.storage.local.get(keys, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        callback(null);
        return;
      }
      const value =
        data && data[key]
          ? String(data[key]).trim()
          : s === "prod" && data && data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY]
            ? String(data[ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY]).trim()
            : "";
      callback(value || null);
    });
  } catch {
    callback(null);
  }
}

function clearAuthToken(server, callback) {
  const s = normalizeServer(server);
  const key = getAuthTokenKey(s);
  const keysToRemove = [key];
  // Back-compat: production may still have a legacy token stored.
  if (s === "prod") keysToRemove.push(ORDER_MANAGER_EXTENSION_TOKEN_STORAGE_KEY_LEGACY);
  try {
    chrome.storage.local.remove(keysToRemove, () => {
      if (callback) callback(!(chrome.runtime && chrome.runtime.lastError));
    });
  } catch {
    if (callback) callback(false);
  }
}

function openAuthPopupForBaseUrl(baseUrl, callback) {
  let authUrl = String(baseUrl || "").trim();
  if (!authUrl) {
    callback(false);
    return;
  }
  if (authUrl.endsWith("/")) authUrl = authUrl.slice(0, -1);
  authUrl += "/extension-auth";

  try {
    if (chrome && chrome.windows && typeof chrome.windows.create === "function") {
      chrome.windows.create({ url: authUrl, type: "popup", width: 500, height: 620 }, () =>
        callback(true)
      );
      return;
    }
  } catch {
    // fall through
  }

  try {
    window.open(authUrl, "_blank", "popup,width=500,height=620");
    callback(true);
  } catch {
    callback(false);
  }
}

function pollForToken(server, timeoutMs, callback) {
  const start = Date.now();
  const key = getAuthTokenKey(server);

  function poll() {
    chrome.storage.local.get([key], (data) => {
      const value = data && data[key] ? String(data[key]).trim() : "";
      if (value) {
        callback(true);
        return;
      }
      if (Date.now() - start >= timeoutMs) {
        callback(false);
        return;
      }
      setTimeout(poll, 700);
    });
  }

  poll();
}

function loadExistingProd(baseUrlEl, statusEl) {
  try {
    chrome.storage.local.get(
      [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY, ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY],
      (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        setStatus(statusEl, "Could not load settings.", "error");
        return;
      }

      const value =
        data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]).trim()
          : data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]
            ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]).trim()
          : "";
      if (baseUrlEl) baseUrlEl.value = value;
      setStatus(statusEl, value ? "Loaded." : "Not set.");
    });
  } catch {
    setStatus(statusEl, "Could not load settings.", "error");
  }
}

function loadExistingDev(enabledEl, fieldsEl, baseUrlEl, statusEl) {
  try {
    chrome.storage.local.get(
      [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY, ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY],
      (data) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          setStatus(statusEl, "Could not load settings.", "error");
          return;
        }
        const enabled = !!(data && data[ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]);
        if (enabledEl) enabledEl.checked = enabled;
        if (fieldsEl) fieldsEl.style.display = enabled ? "block" : "none";

        const value =
          data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
            ? String(data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]).trim()
            : "";
        if (baseUrlEl) baseUrlEl.value = value;
        setStatus(statusEl, enabled ? (value ? "Loaded." : "Not set.") : "Disabled.");
      }
    );
  } catch {
    setStatus(statusEl, "Could not load settings.", "error");
  }
}

function saveProd(baseUrlEl, statusEl) {
  let normalized;
  try {
    normalized = normalizeBaseUrl(baseUrlEl ? baseUrlEl.value : "");
  } catch (e) {
    setStatus(
      statusEl,
      String(e && e.message ? e.message : e),
      "error"
    );
    return;
  }

  chrome.storage.local.set(
    {
      // New key
      [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]: normalized,
      // Back-compat for older versions that only know one base URL
      [ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]: normalized,
    },
    () => {
      if (chrome.runtime && chrome.runtime.lastError) {
        setStatus(statusEl, "Save failed.", "error");
        return;
      }
      if (baseUrlEl) baseUrlEl.value = normalized;
      setStatus(statusEl, "Saved.", "ok");
    }
  );
}

function saveDev(enabledEl, fieldsEl, baseUrlEl, statusEl) {
  let normalized;
  try {
    normalized = normalizeBaseUrl(baseUrlEl ? baseUrlEl.value : "");
  } catch (e) {
    setStatus(
      statusEl,
      String(e && e.message ? e.message : e),
      "error"
    );
    return;
  }

  chrome.storage.local.set(
    {
      [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]: true,
      [ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]: normalized,
    },
    () => {
      if (chrome.runtime && chrome.runtime.lastError) {
        setStatus(statusEl, "Save failed.", "error");
        return;
      }
      if (enabledEl) enabledEl.checked = true;
      if (fieldsEl) fieldsEl.style.display = "block";
      if (baseUrlEl) baseUrlEl.value = normalized;
      setStatus(statusEl, "Saved.", "ok");
    }
  );
}

function clearProd(baseUrlEl, statusEl) {
  chrome.storage.local.remove(
    [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY, ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY],
    () => {
    if (chrome.runtime && chrome.runtime.lastError) {
      setStatus(statusEl, "Clear failed.", "error");
      return;
    }
    if (baseUrlEl) baseUrlEl.value = "";
    setStatus(statusEl, "Cleared.", "ok");
  });
}

function clearDev(baseUrlEl, statusEl) {
  chrome.storage.local.remove([ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY], () => {
    if (chrome.runtime && chrome.runtime.lastError) {
      setStatus(statusEl, "Clear failed.", "error");
      return;
    }
    if (baseUrlEl) baseUrlEl.value = "";
    setStatus(statusEl, "Cleared.", "ok");
  });
}

function setDevEnabled(enabled, fieldsEl, statusEl) {
  chrome.storage.local.set({ [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY]: !!enabled }, () => {
    if (chrome.runtime && chrome.runtime.lastError) {
      setStatus(statusEl, "Save failed.", "error");
      return;
    }
    if (fieldsEl) fieldsEl.style.display = enabled ? "block" : "none";
    if (!enabled) setStatus(statusEl, "Disabled.");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const baseUrlProdEl = document.getElementById("baseUrlProd");
  const statusProdEl = document.getElementById("statusProd");
  const saveProdBtn = document.getElementById("saveProd");
  const clearProdBtn = document.getElementById("clearProd");

  const devEnabledEl = document.getElementById("devEnabled");
  const devFieldsEl = document.getElementById("devFields");
  const baseUrlDevEl = document.getElementById("baseUrlDev");
  const statusDevEl = document.getElementById("statusDev");
  const saveDevBtn = document.getElementById("saveDev");
  const clearDevBtn = document.getElementById("clearDev");

  const authStatusProdEl = document.getElementById("authStatusProd");
  const authStatusDevEl = document.getElementById("authStatusDev");
  const authStatusGlobalProdEl = document.getElementById("authStatusGlobalProd");
  const authStatusGlobalDevEl = document.getElementById("authStatusGlobalDev");
  const authLoginProdBtn = document.getElementById("authLoginProd");
  const authLogoutProdBtn = document.getElementById("authLogoutProd");
  const authLoginDevBtn = document.getElementById("authLoginDev");
  const authLogoutDevBtn = document.getElementById("authLogoutDev");

  loadExistingProd(baseUrlProdEl, statusProdEl);
  loadExistingDev(devEnabledEl, devFieldsEl, baseUrlDevEl, statusDevEl);

  function refreshAuthStatus() {
    getAuthToken("prod", (prodToken) => {
      setHint(authStatusProdEl, prodToken ? "Logged in" : "Logged out");
      if (authLoginProdBtn) authLoginProdBtn.style.display = prodToken ? "none" : "";
      if (authLogoutProdBtn) authLogoutProdBtn.style.display = prodToken ? "" : "none";
    });

    getServerBaseUrlFromStorage("dev", ({ enabled, baseUrl }) => {
      getAuthToken("dev", (devToken) => {
        if (!enabled) {
          setHint(authStatusDevEl, "Disabled (enable dev mode above)");
        } else if (!baseUrl) {
          setHint(authStatusDevEl, "Not set (save a dev server URL above)");
        } else {
          setHint(authStatusDevEl, devToken ? "Logged in" : "Logged out");
        }

        const devUsable = enabled && !!baseUrl;
        if (authLoginDevBtn) {
          authLoginDevBtn.disabled = !devUsable;
          authLoginDevBtn.style.display = devToken ? "none" : "";
        }
        if (authLogoutDevBtn) {
          authLogoutDevBtn.disabled = !devUsable;
          authLogoutDevBtn.style.display = devToken ? "" : "none";
        }
      });
    });
  }

  refreshAuthStatus();

  if (saveProdBtn) {
    saveProdBtn.addEventListener("click", () => {
      saveProd(baseUrlProdEl, statusProdEl);
      setTimeout(refreshAuthStatus, 0);
    });
  }

  if (clearProdBtn) {
    clearProdBtn.addEventListener("click", () => {
      clearProd(baseUrlProdEl, statusProdEl);
      setTimeout(refreshAuthStatus, 0);
    });
  }

  if (baseUrlProdEl) {
    baseUrlProdEl.addEventListener("keydown", (e) => {
      if (e && e.key === "Enter") {
        e.preventDefault();
        saveProd(baseUrlProdEl, statusProdEl);
        setTimeout(refreshAuthStatus, 0);
      }
    });
  }

  if (devEnabledEl) {
    devEnabledEl.addEventListener("change", () => {
      setDevEnabled(!!devEnabledEl.checked, devFieldsEl, statusDevEl);
      setTimeout(refreshAuthStatus, 0);
    });
  }

  if (saveDevBtn) {
    saveDevBtn.addEventListener("click", () =>
      saveDev(devEnabledEl, devFieldsEl, baseUrlDevEl, statusDevEl)
    );
  }

  if (clearDevBtn) {
    clearDevBtn.addEventListener("click", () => {
      clearDev(baseUrlDevEl, statusDevEl);
      setTimeout(refreshAuthStatus, 0);
    });
  }

  if (baseUrlDevEl) {
    baseUrlDevEl.addEventListener("keydown", (e) => {
      if (e && e.key === "Enter") {
        e.preventDefault();
        saveDev(devEnabledEl, devFieldsEl, baseUrlDevEl, statusDevEl);
        setTimeout(refreshAuthStatus, 0);
      }
    });
  }

  if (saveDevBtn) {
    saveDevBtn.addEventListener("click", () => setTimeout(refreshAuthStatus, 0));
  }

  if (authLogoutProdBtn) {
    authLogoutProdBtn.addEventListener("click", () => {
      setStatus(authStatusGlobalProdEl, "Logging out of production…");
      clearAuthToken("prod", () => {
        setStatus(authStatusGlobalProdEl, "Logged out of production.", "ok");
        refreshAuthStatus();
      });
    });
  }

  if (authLogoutDevBtn) {
    authLogoutDevBtn.addEventListener("click", () => {
      setStatus(authStatusGlobalDevEl, "Logging out of dev…");
      clearAuthToken("dev", () => {
        setStatus(authStatusGlobalDevEl, "Logged out of dev.", "ok");
        refreshAuthStatus();
      });
    });
  }

  if (authLoginProdBtn) {
    authLoginProdBtn.addEventListener("click", () => {
      setStatus(authStatusGlobalProdEl, "Opening production authorization…");
      getServerBaseUrlFromStorage("prod", ({ baseUrl }) => {
        if (!baseUrl) {
          setStatus(authStatusGlobalProdEl, "Production base URL is not configured above.", "error");
          return;
        }
        openAuthPopupForBaseUrl(baseUrl, (ok) => {
          if (!ok) {
            setStatus(authStatusGlobalProdEl, "Could not open authorization window.", "error");
            return;
          }
          setStatus(authStatusGlobalProdEl, "Sign in and authorize in the popup window.");
          pollForToken("prod", 60000, (found) => {
            setStatus(
              authStatusGlobalProdEl,
              found ? "Logged in to production." : "Timed out waiting for authorization.",
              found ? "ok" : "error"
            );
            refreshAuthStatus();
          });
        });
      });
    });
  }

  if (authLoginDevBtn) {
    authLoginDevBtn.addEventListener("click", () => {
      setStatus(authStatusGlobalDevEl, "Opening dev authorization…");
      getServerBaseUrlFromStorage("dev", ({ enabled, baseUrl }) => {
        if (!enabled) {
          setStatus(authStatusGlobalDevEl, "Dev mode is disabled above.", "error");
          return;
        }
        if (!baseUrl) {
          setStatus(authStatusGlobalDevEl, "Dev base URL is not configured above.", "error");
          return;
        }
        openAuthPopupForBaseUrl(baseUrl, (ok) => {
          if (!ok) {
            setStatus(authStatusGlobalDevEl, "Could not open authorization window.", "error");
            return;
          }
          setStatus(authStatusGlobalDevEl, "Sign in and authorize in the popup window.");
          pollForToken("dev", 60000, (found) => {
            setStatus(
              authStatusGlobalDevEl,
              found ? "Logged in to dev." : "Timed out waiting for authorization.",
              found ? "ok" : "error"
            );
            refreshAuthStatus();
          });
        });
      });
    });
  }
});

