const WALMART_BULK_JOB_STORAGE_KEY = "walmartBulkJob";

function $(id) {
  return document.getElementById(id);
}

function setStatus(state, text) {
  const dot = $("statusDot");
  const label = $("statusText");
  if (!dot || !label) return;
  dot.classList.remove("ok", "err");
  if (state === "ok") dot.classList.add("ok");
  if (state === "err") dot.classList.add("err");
  label.textContent = text || "";
}

function appendLog(text, status, meta) {
  const log = $("log");
  if (!log) return;
  const row = document.createElement("div");
  row.className = "logRow";

  const badge = document.createElement("div");
  badge.className = "badge";
  if (status === "ok") badge.classList.add("ok");
  if (status === "err") badge.classList.add("err");

  const msgWrap = document.createElement("div");
  msgWrap.className = "msg";
  msgWrap.textContent = text;

  if (meta) {
    const m = document.createElement("div");
    m.className = "meta";
    m.textContent = meta;
    msgWrap.appendChild(m);
  }

  row.appendChild(badge);
  row.appendChild(msgWrap);
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function setCounts({ total, done, ok, err }) {
  if (typeof total === "number") $("statTotal").textContent = String(total);
  if (typeof done === "number") $("statDone").textContent = String(done);
  if (typeof ok === "number") $("statOk").textContent = String(ok);
  if (typeof err === "number") $("statErr").textContent = String(err);

  if (typeof total === "number" && total > 0 && typeof done === "number") {
    const pct = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
    $("barFill").style.width = pct + "%";
  }
}

async function loadJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.get(WALMART_BULK_JOB_STORAGE_KEY, (s) => {
        resolve(s && s[WALMART_BULK_JOB_STORAGE_KEY] ? s[WALMART_BULK_JOB_STORAGE_KEY] : null);
      });
    } catch {
      resolve(null);
    }
  });
}

async function clearJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(WALMART_BULK_JOB_STORAGE_KEY, () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

(async function main() {
  const closeBtn = $("closeBtn");
  const cancelBtn = $("cancelBtn");
  const currentText = $("currentText");
  const reviewLink = $("reviewLink");

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      try {
        window.close();
      } catch {
        // ignore
      }
    });
  }

  setStatus("pending", "Preparing…");
  appendLog("Loading bulk import job…", "pending");

  let job = await loadJob();
  if (!job) {
    setStatus("err", "No job found");
    appendLog("No bulk job found. Start from the Walmart orders list popup.", "err");
    if (closeBtn) closeBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = true;
    return;
  }

  const initialOrders = Array.isArray(job.orderNumbers) ? job.orderNumbers : [];
  if (initialOrders.length > 0) {
    setCounts({ total: initialOrders.length, done: 0, ok: 0, err: 0 });
    if (currentText) currentText.textContent = "Ready to start.";
    appendLog("Found " + initialOrders.length + " order(s). Starting…", "pending");
  } else {
    $("statTotal").textContent = "…";
    setCounts({ done: 0, ok: 0, err: 0 });
    setStatus("pending", "Waiting…");
    if (currentText) currentText.textContent = "Collecting order numbers from the orders list…";
    appendLog("Collecting order numbers from the orders list…", "pending");
  }

  let port;
  try {
    port = chrome.runtime.connect({ name: "walmartBulkImport" });
  } catch (e) {
    setStatus("err", "Could not connect");
    appendLog("Could not connect to background worker.", "err", String(e && e.message ? e.message : e));
    if (closeBtn) closeBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = true;
    return;
  }

  let cancelRequested = false;
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      if (cancelRequested) return;
      cancelRequested = true;
      cancelBtn.disabled = true;
      setStatus("pending", "Cancelling…");
      appendLog("Cancel requested. Stopping background job…", "pending");
      if (currentText) currentText.textContent = "Cancelling…";
      try {
        port.postMessage({ type: "cancel", store: "walmart" });
      } catch (e) {
        appendLog("Could not send cancel request.", "err", String(e && e.message ? e.message : e));
        if (closeBtn) closeBtn.disabled = false;
      }
    });
  }

  let done = 0;
  let ok = 0;
  let err = 0;
  let total = initialOrders.length || null;

  function onProgressMessage(msg) {
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "jobStarted") {
      setStatus("pending", "Running…");
      if (currentText) currentText.textContent = "Extracting order details…";
      return;
    }

    if (msg.type === "collectingOrderNumbers") {
      setStatus("pending", "Collecting…");
      if (currentText) currentText.textContent = "Collecting order numbers from the orders list…";
      appendLog("Collecting order numbers from the orders list…", "pending");
      return;
    }

    if (msg.type === "orderNumbersPageProgress") {
      const page = typeof msg.page === "number" ? msg.page : parseInt(String(msg.page || ""), 10);
      const extracted =
        typeof msg.extracted === "number" ? msg.extracted : parseInt(String(msg.extracted || ""), 10);
      const totalSoFar =
        typeof msg.total === "number" ? msg.total : parseInt(String(msg.total || ""), 10);
      const maxPages =
        typeof msg.maxPages === "number" ? msg.maxPages : parseInt(String(msg.maxPages || ""), 10);

      const pageLabel = Number.isFinite(page) && page > 0 ? String(page) : "?";
      const maxLabel = Number.isFinite(maxPages) && maxPages > 0 ? String(maxPages) : "?";

      const extractedLabel = Number.isFinite(extracted) ? extracted : 0;
      const totalLabel = Number.isFinite(totalSoFar) ? totalSoFar : null;

      if (currentText) {
        currentText.textContent =
          "Orders list received (page " +
          pageLabel +
          "/" +
          maxLabel +
          "): +" +
          extractedLabel +
          (totalLabel != null ? " (" + totalLabel + " total)" : "");
      }

      appendLog(
        "Orders list received (page " +
          pageLabel +
          "/" +
          maxLabel +
          "): extracted " +
          extractedLabel +
          " order(s).",
        "pending",
        totalLabel != null ? "Total extracted so far: " + String(totalLabel) : null
      );
      return;
    }

    if (msg.type === "orderNumbersReady") {
      const t =
        typeof msg.total === "number"
          ? msg.total
          : parseInt(String(msg.total || ""), 10);
      if (Number.isFinite(t) && t > 0) {
        total = t;
        setCounts({ total, done, ok, err });
        appendLog("Found " + total + " order(s). Collecting details…", "pending");
      }
      return;
    }

    if (msg.type === "orderStatus") {
      const orderNumber = msg.orderNumber ? String(msg.orderNumber) : "";
      const status = msg.status;
      if (status === "pending") {
        if (currentText) currentText.textContent = "Collecting " + orderNumber + "…";
        appendLog("Collecting " + orderNumber + "…", "pending");
      } else if (status === "ok") {
        ok++;
        done++;
        setCounts({ total, done, ok, err });
        appendLog("Order " + orderNumber + ": collected.", "ok");
      } else if (status === "error") {
        err++;
        done++;
        setCounts({ total, done, ok, err });
        appendLog(
          "Order " + orderNumber + ": failed.",
          "err",
          msg.error ? String(msg.error) : null
        );
      }
      return;
    }

    if (msg.type === "reviewReady") {
      const url = msg.url ? String(msg.url) : "";
      if (url && reviewLink) {
        reviewLink.style.display = "";
        reviewLink.href = url;
      }
      appendLog("Opened bulk review in Order Manager.", "ok");
      return;
    }

    if (msg.type === "jobDone") {
      setStatus(ok > 0 && err === 0 ? "ok" : err > 0 ? "err" : "ok", "Finished");
      if (currentText) currentText.textContent = "Finished.";
      appendLog("Finished.", "ok");

      if (closeBtn) closeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = true;

      // Best-effort cleanup: clear job payload to avoid replays.
      clearJob().finally(() => {});
      return;
    }

    if (msg.type === "jobCancelled") {
      setStatus("err", "Cancelled");
      appendLog("Cancelled.", "err");
      if (currentText) currentText.textContent = "Cancelled. You can close this tab.";
      if (closeBtn) closeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = true;
      clearJob().finally(() => {});
      return;
    }

    if (msg.type === "jobError") {
      setStatus("err", "Failed");
      appendLog("Bulk import failed.", "err", msg.error ? String(msg.error) : null);
      if (currentText) currentText.textContent = "Failed. You can close this tab.";
      if (closeBtn) closeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = true;
      return;
    }
  }

  port.onMessage.addListener(onProgressMessage);
  port.onDisconnect.addListener(() => {
    setStatus("err", "Disconnected");
    appendLog("Disconnected from background worker.", "err");
    if (closeBtn) closeBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = true;
  });

  try {
    port.postMessage({
      type: "start",
      store: "walmart",
      orderNumbers: job.orderNumbers,
      sourceTabId: job.sourceTabId || null,
      maxPages: job.maxPages || null,
      createdAt: job.createdAt || null,
    });
  } catch (e) {
    setStatus("err", "Could not start");
    appendLog("Could not start background job.", "err", String(e && e.message ? e.message : e));
    if (closeBtn) closeBtn.disabled = false;
  }
})();

