document.getElementById("getOrders").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    await chrome.tabs.sendMessage(tab.id, { action: "getOrderNumbers" });
  } catch {
    alert("Order Manager: Open walmart.com/orders first, then try again.");
  }
});
