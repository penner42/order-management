const AMAZON_BULK_JOB_STORAGE_KEY = "amazonBulkJob";

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

function setCounts({ total, ok, err, page, maxPages }) {
  if (typeof total === "number") $("statTotal").textContent = String(total);
  if (typeof ok === "number") $("statOk").textContent = String(ok);
  if (typeof err === "number") $("statErr").textContent = String(err);
  if (typeof page === "number" && typeof maxPages === "number") {
    $("statPage").textContent = page + "/" + maxPages;
  }

  if (typeof total === "number" && total > 0 && typeof ok === "number") {
    const done = ok + (typeof err === "number" ? err : 0);
    const pct = Math.max(0, Math.min(100, Math.round((done / Math.max(total, done)) * 100)));
    $("barFill").style.width = pct + "%";
  }
}

async function loadJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.get(AMAZON_BULK_JOB_STORAGE_KEY, (s) => {
        resolve(s && s[AMAZON_BULK_JOB_STORAGE_KEY] ? s[AMAZON_BULK_JOB_STORAGE_KEY] : null);
      });
    } catch {
      resolve(null);
    }
  });
}

async function clearJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(AMAZON_BULK_JOB_STORAGE_KEY, () => resolve(null));
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

  const job = await loadJob();
  if (!job) {
    setStatus("err", "No job found");
    appendLog("No bulk job found. Start from the Amazon order history popup.", "err");
    if (closeBtn) closeBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = true;
    return;
  }

  const maxPages = typeof job.maxPages === "number" ? job.maxPages : 1;
  setCounts({ total: 0, ok: 0, err: 0, page: 1, maxPages });
  if (currentText) currentText.textContent = "Starting from your current order history page…";

  let port;
  try {
    port = chrome.runtime.connect({ name: "amazonBulkImport" });
  } catch (e) {
    setStatus("err", "Could not connect");
    appendLog("Could not connect to background worker.", "err", String(e && e.message ? e.message : e));
    if (closeBtn) closeBtn.disabled = false;
    return;
  }

  let cancelRequested = false;
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      if (cancelRequested) return;
      cancelRequested = true;
      cancelBtn.disabled = true;
      setStatus("pending", "Cancelling…");
      appendLog("Cancel requested…", "pending");
      try {
        port.postMessage({ type: "cancel", store: "amazon" });
      } catch {
        // ignore
      }
    });
  }

  let ok = 0;
  let err = 0;
  let total = 0;

  port.onMessage.addListener((msg) => {
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "jobStarted") {
      setStatus("pending", "Running…");
      appendLog("Import started.", "pending");
      return;
    }

    if (msg.type === "pageStatus") {
      const page = msg.page || "?";
      const max = msg.maxPages || maxPages;
      setCounts({ total, ok, err, page, maxPages: max });
      if (msg.status === "pending") {
        appendLog("Processing list page " + page + "/" + max + "…", "pending");
        if (currentText) currentText.textContent = "Reading orders on page " + page + "…";
      } else if (msg.noMore) {
        appendLog("No more pages available.", "pending");
      } else {
        appendLog("Finished page " + page + ".", "ok");
      }
      return;
    }

    if (msg.type === "pageOrdersReady") {
      appendLog(
        "Page " + msg.page + ": found " + (msg.count != null ? msg.count : 0) + " order(s).",
        "pending"
      );
      return;
    }

    if (msg.type === "orderStatus") {
      const orderNumber = msg.orderNumber ? String(msg.orderNumber) : "";
      if (msg.status === "pending") {
        if (currentText) currentText.textContent = "Importing " + orderNumber + "…";
        appendLog("Importing " + orderNumber + "…", "pending");
      } else if (msg.status === "ok") {
        ok++;
        total = ok + err;
        setCounts({ total, ok, err, page: msg.page, maxPages: msg.maxPages || maxPages });
        appendLog("Order " + orderNumber + ": collected.", "ok");
      } else if (msg.status === "error") {
        err++;
        total = ok + err;
        setCounts({ total, ok, err, page: msg.page, maxPages: msg.maxPages || maxPages });
        appendLog("Order " + orderNumber + ": failed.", "err", msg.error ? String(msg.error) : null);
      }
      return;
    }

    if (msg.type === "counts") {
      if (typeof msg.ok === "number") ok = msg.ok;
      if (typeof msg.err === "number") err = msg.err;
      if (typeof msg.done === "number") total = msg.done;
      setCounts({ total, ok, err });
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
      clearJob();
      return;
    }

    if (msg.type === "jobCancelled") {
      setStatus("err", "Cancelled");
      appendLog("Cancelled.", "err");
      if (closeBtn) closeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = true;
      clearJob();
      return;
    }

    if (msg.type === "jobError") {
      setStatus("err", "Failed");
      appendLog("Bulk import failed.", "err", msg.error ? String(msg.error) : null);
      if (closeBtn) closeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = true;
    }
  });

  port.onDisconnect.addListener(() => {
    setStatus("err", "Disconnected");
    appendLog("Disconnected from background worker.", "err");
    if (closeBtn) closeBtn.disabled = false;
  });

  try {
    port.postMessage({
      type: "start",
      store: "amazon",
      sourceTabId: job.sourceTabId || null,
      maxPages: job.maxPages || 1,
      createdAt: job.createdAt || null,
    });
  } catch (e) {
    setStatus("err", "Could not start");
    appendLog("Could not start background job.", "err", String(e && e.message ? e.message : e));
    if (closeBtn) closeBtn.disabled = false;
  }
})();
