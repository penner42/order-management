const COSTCO_BULK_JOB_STORAGE_KEY = 'costcoBulkJob'

function $(id) {
  return document.getElementById(id)
}

function setStatus(state, text) {
  const dot = $('statusDot')
  const label = $('statusText')
  if (!dot || !label) return
  dot.classList.remove('ok', 'err')
  if (state === 'ok') dot.classList.add('ok')
  if (state === 'err') dot.classList.add('err')
  label.textContent = text || ''
}

function appendLog(text, status, meta) {
  const log = $('log')
  if (!log) return
  const row = document.createElement('div')
  row.className = 'logRow'

  const badge = document.createElement('div')
  badge.className = 'badge'
  if (status === 'ok') badge.classList.add('ok')
  if (status === 'err') badge.classList.add('err')

  const msgWrap = document.createElement('div')
  msgWrap.className = 'msg'
  msgWrap.textContent = text

  if (meta) {
    const m = document.createElement('div')
    m.className = 'meta'
    m.textContent = meta
    msgWrap.appendChild(m)
  }

  row.appendChild(badge)
  row.appendChild(msgWrap)
  log.appendChild(row)
  log.scrollTop = log.scrollHeight
}

async function loadJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.get(COSTCO_BULK_JOB_STORAGE_KEY, (s) => {
        resolve(s && s[COSTCO_BULK_JOB_STORAGE_KEY] ? s[COSTCO_BULK_JOB_STORAGE_KEY] : null)
      })
    } catch {
      resolve(null)
    }
  })
}

async function clearJob() {
  return await new Promise((resolve) => {
    try {
      chrome.storage.local.remove(COSTCO_BULK_JOB_STORAGE_KEY, () => resolve(null))
    } catch {
      resolve(null)
    }
  })
}

function setSummaryOrders(n) {
  const el = $('statOrders')
  if (el) el.textContent = typeof n === 'number' ? String(n) : '—'
}

function setSummaryCaptured(v) {
  const el = $('statCaptured')
  if (!el) return
  if (v === true) el.textContent = 'Yes'
  else if (v === false) el.textContent = 'No'
  else el.textContent = '—'
}

;(async function main() {
  const closeBtn = $('closeBtn')
  const cancelBtn = $('cancelBtn')
  const reviewLink = $('reviewLink')

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      try {
        window.close()
      } catch {}
    })
  }

  setStatus('pending', 'Preparing…')
  appendLog('Loading bulk import job…', 'pending')

  const job = await loadJob()
  if (!job || typeof job.sourceTabId !== 'number') {
    setStatus('err', 'No job found')
    appendLog('No bulk job found. Start from the Costco orders page popup.', 'err')
    if (closeBtn) closeBtn.disabled = false
    if (cancelBtn) cancelBtn.disabled = true
    return
  }

  let port
  try {
    port = chrome.runtime.connect({ name: 'costcoBulkImport' })
  } catch (e) {
    setStatus('err', 'Could not connect')
    appendLog('Could not connect to background worker.', 'err', String(e && e.message ? e.message : e))
    if (closeBtn) closeBtn.disabled = false
    if (cancelBtn) cancelBtn.disabled = true
    return
  }

  let cancelRequested = false
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      if (cancelRequested) return
      cancelRequested = true
      cancelBtn.disabled = true
      setStatus('pending', 'Cancelling…')
      appendLog('Cancel requested. Stopping background job…', 'pending')
      try {
        port.postMessage({ type: 'cancel', store: 'costco' })
      } catch (e) {
        appendLog('Could not send cancel request.', 'err', String(e && e.message ? e.message : e))
        if (closeBtn) closeBtn.disabled = false
      }
    })
  }

  function onMsg(msg) {
    if (!msg || typeof msg !== 'object') return

    if (msg.type === 'jobStarted') {
      setStatus('pending', 'Running…')
      appendLog('Started.', 'pending')
      return
    }

    if (msg.type === 'extracting') {
      setStatus('pending', 'Extracting…')
      appendLog('Requesting captured Costco GraphQL payload…', 'pending')
      return
    }

    if (msg.type === 'normalizing') {
      setStatus('pending', 'Normalizing…')
      const cached = msg.cached === true
      appendLog('Normalizing captured payload…', 'pending', cached ? 'Using cached capture.' : 'Using fresh capture.')
      return
    }

  if (msg.type === 'enriching') {
    setStatus('pending', 'Fetching details…')
    const total = typeof msg.total === 'number' ? msg.total : null
    appendLog('Opening each order details page to capture quantities/prices…', 'pending', total != null ? String(total) + ' order(s)' : null)
    return
  }

  if (msg.type === 'enrichingTabReady') {
    appendLog('Opened Costco detail capture tab.', 'ok')
    return
  }

  if (msg.type === 'enrichingTabError') {
    appendLog('Could not open Costco detail capture tab.', 'err', msg.error ? String(msg.error) : null)
    return
  }

  if (msg.type === 'orderDetailsStatus') {
    const orderNumber = msg.orderNumber ? String(msg.orderNumber) : '—'
    const status = msg.status ? String(msg.status) : 'pending'
    if (status === 'ok') {
      appendLog('Captured details for order ' + orderNumber + '.', 'ok')
    } else if (status === 'error') {
      appendLog('Failed to capture details for order ' + orderNumber + '.', 'err', msg.error ? String(msg.error) : null)
    } else {
      appendLog('Capturing details for order ' + orderNumber + '…', 'pending')
    }
    return
  }

    if (msg.type === 'creatingSession') {
      setStatus('pending', 'Creating session…')
      const total = typeof msg.total === 'number' ? msg.total : null
      setSummaryOrders(total)
      setSummaryCaptured(true)
      appendLog('Creating bulk import session…', 'pending', total != null ? String(total) + ' order(s)' : null)
      return
    }

    if (msg.type === 'reviewReady') {
      const url = msg.url ? String(msg.url) : ''
      if (url && reviewLink) {
        reviewLink.style.display = ''
        reviewLink.href = url
      }
      appendLog('Opened bulk review in Order Manager.', 'ok')
      return
    }

    if (msg.type === 'jobDone') {
      setStatus('ok', 'Finished')
      appendLog('Finished.', 'ok')
      if (closeBtn) closeBtn.disabled = false
      if (cancelBtn) cancelBtn.disabled = true
      clearJob()
      return
    }

    if (msg.type === 'jobCancelled') {
      setStatus('err', 'Cancelled')
      appendLog('Cancelled.', 'err')
      if (closeBtn) closeBtn.disabled = false
      if (cancelBtn) cancelBtn.disabled = true
      clearJob()
      return
    }

    if (msg.type === 'jobError') {
      setStatus('err', 'Error')
      appendLog('Bulk import failed.', 'err', msg.error ? String(msg.error) : null)
      setSummaryCaptured(false)
      if (closeBtn) closeBtn.disabled = false
      if (cancelBtn) cancelBtn.disabled = true
      clearJob()
    }
  }

  try {
    port.onMessage.addListener(onMsg)
  } catch {}

  setStatus('pending', 'Starting…')
  appendLog('Starting background job…', 'pending')

  try {
    port.postMessage({ type: 'start', store: 'costco', sourceTabId: job.sourceTabId })
  } catch (e) {
    setStatus('err', 'Error')
    appendLog('Could not start background job.', 'err', String(e && e.message ? e.message : e))
    if (closeBtn) closeBtn.disabled = false
    if (cancelBtn) cancelBtn.disabled = true
  }
})()

