() => {
  const lines = node => (node?.innerText || node?.textContent || '')
    .split('\n').map(line => line.trim()).filter(Boolean);
  const label = value => value.replace(/:$/, '').trim().toLowerCase();
  const ancestors = function* (node) {
    for (let current = node; current && current !== document.body; current = current.parentElement) {
      yield current;
    }
  };
  const leaves = [...document.querySelectorAll('span, h1, h2, h3, h4, p, dt, div, strong')]
    .filter(node => !node.children.length && node.getClientRects().length);
  const named = (name, root) => leaves.filter(node =>
    (!root || root.contains(node)) && label(node.textContent.trim()) === name);
  const valueFor = (name, root) => {
    const node = named(name, root)[0];
    if (node) {
      const value = lines(node.nextElementSibling).join(' ');
      if (value) return value;
    }
    for (const line of lines(root)) {
      const colon = line.indexOf(':');
      if (colon !== -1 && label(line.slice(0, colon)) === name) {
        const value = line.slice(colon + 1).trim();
        if (value) return value;
      }
    }
    return null;
  };
  const addressFor = heading => {
    if (!heading) return [];
    return lines(heading.nextElementSibling);
  };
  const contactsFor = root => {
    const email = valueFor('email address', root) || valueFor('email', root);
    const phone = valueFor('phone number', root) || valueFor('phone', root);
    return {
      email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email || '') ? email : null,
      phone: /^(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}$/.test(phone || '') ? phone : null,
    };
  };
  const productLinkFor = root => [...root.querySelectorAll('a[href]')].find(link => {
    const url = new URL(link.href);
    return url.origin === 'https://www.bestbuy.com' && /^\/(product|site)\//.test(url.pathname) &&
      lines(link).length > 0;
  });
  const shipmentGroups = new Map();
  const rows = named('sku').map(sku => {
    const parents = [...ancestors(sku)];
    const item = parents.find(node => named('item total', node).length && named('sku', node).length === 1);
    const product = parents.filter(node => named('sku', node).length === 1).map(productLinkFor).find(Boolean);
    const group = parents.find(node => named('tracking info', node).length === 1) ||
      parents.find(node => named('shipping address', node).length === 1);
    if (group && !shipmentGroups.has(group)) {
      const tracking = named('tracking number', group)[0];
      const trackingLink = tracking?.parentElement?.querySelector('a[href^="https://"]');
      const statusScope = group.firstElementChild;
      const status = lines(statusScope).find(line =>
        /^(?:order (?:placed|received)|preparing|getting|we.ll provide tracking|in progress|shipped|delivered|cancel(?:ed|led)|ready for pickup)\b/i.test(line));
      shipmentGroups.set(group, {
        tracking: valueFor('tracking number', group),
        trackingUrl: trackingLink?.href || null,
        status: status || null,
        address: addressFor(named('shipping address', group)[0]),
        contact: contactsFor(group),
        deliveredDate: valueFor('delivered on', group) || valueFor('delivery date', group) ||
          status?.match(/^delivered(?: on)?\s+(.+)$/i)?.[1] || null,
      });
    }
    return {
      sku: lines(sku.nextElementSibling).join(' '),
      model: item && valueFor('model', item),
      name: product && lines(product).join(' '),
      productUrl: product && new URL(product.href).origin + new URL(product.href).pathname,
      quantity: item && valueFor('quantity', item),
      itemTotal: item && valueFor('item total', item),
      productPrice: item && valueFor('product price', item),
      shipping: item && valueFor('shipping', item),
      groupIndex: group ? [...shipmentGroups.keys()].indexOf(group) : -1,
    };
  });
  const paymentHeading = named('payment method')[0];
  const payment = paymentHeading?.parentElement?.parentElement;
  const summaryHeading = named('order summary')[0];
  const summary = summaryHeading?.parentElement?.parentElement;
  return {
    orderNumber: valueFor('order number', document.body),
    purchaseDate: valueFor('purchase date', document.body),
    total: (summary && valueFor('order total', summary)) || valueFor('total', document.body),
    subtotal: summary && valueFor('product total', summary),
    shipping: summary && valueFor('shipping', summary),
    tax: summary && (valueFor('sales tax, fees & surcharges', summary) || valueFor('sales tax', summary)),
    discount: summary && (valueFor('order discount', summary) || valueFor('discount', summary)),
    payment: payment ? lines(payment) : [],
    rows,
    groups: [...shipmentGroups.values()],
  };
}
