const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY = "orderManagerApiBaseUrl";

function setStatus(el, message, kind) {
  if (!el) return;
  el.textContent = message || "";
  el.classList.remove("ok", "error");
  if (kind === "ok") el.classList.add("ok");
  if (kind === "error") el.classList.add("error");
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

function loadExisting(baseUrlEl, statusEl) {
  try {
    chrome.storage.local.get(ORDER_MANAGER_API_BASE_URL_STORAGE_KEY, (data) => {
      if (chrome.runtime && chrome.runtime.lastError) {
        setStatus(statusEl, "Could not load settings.", "error");
        return;
      }

      const value =
        data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY]
          ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY]).trim()
          : "";
      if (baseUrlEl) baseUrlEl.value = value;
      setStatus(statusEl, value ? "Loaded." : "Not set.");
    });
  } catch {
    setStatus(statusEl, "Could not load settings.", "error");
  }
}

function save(baseUrlEl, statusEl) {
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
    { [ORDER_MANAGER_API_BASE_URL_STORAGE_KEY]: normalized },
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

function clear(baseUrlEl, statusEl) {
  chrome.storage.local.remove(ORDER_MANAGER_API_BASE_URL_STORAGE_KEY, () => {
    if (chrome.runtime && chrome.runtime.lastError) {
      setStatus(statusEl, "Clear failed.", "error");
      return;
    }
    if (baseUrlEl) baseUrlEl.value = "";
    setStatus(statusEl, "Cleared.", "ok");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const baseUrlEl = document.getElementById("baseUrl");
  const statusEl = document.getElementById("status");
  const saveBtn = document.getElementById("save");
  const clearBtn = document.getElementById("clear");

  loadExisting(baseUrlEl, statusEl);

  if (saveBtn) {
    saveBtn.addEventListener("click", () => save(baseUrlEl, statusEl));
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => clear(baseUrlEl, statusEl));
  }

  if (baseUrlEl) {
    baseUrlEl.addEventListener("keydown", (e) => {
      if (e && e.key === "Enter") {
        e.preventDefault();
        save(baseUrlEl, statusEl);
      }
    });
  }
});

