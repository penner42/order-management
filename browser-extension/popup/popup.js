document.getElementById("getOrders").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const pageCount = parseInt(
    document.getElementById("pageCount").value || "1",
    10
  );
  const safePageCount = Math.max(1, isNaN(pageCount) ? 1 : pageCount);
  try {
    await chrome.tabs.sendMessage(tab.id, {
      action: "getOrderNumbers",
      pageCount: safePageCount,
    });
  } catch {
    alert("Order Manager: Open walmart.com/orders first, then try again.");
  }
});
