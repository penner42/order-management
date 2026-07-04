;(function () {
  'use strict'

  /** @type {Record<string, string | string[]>} */
  const Selectors = {
    ORDER_CARD: [
      'div.order-card',
      'div.order',
      '[data-component="orderCard"]',
      '.a-box-group.order',
      '#ordersContainer .order-card',
      '.js-order-card',
    ],
    ORDER_DETAILS_ROOT: ['div#orderDetails', 'div#ordersContainer'],
    ORDER_ID: [
      '[data-component="orderId"]',
      '.yohtmlc-order-id :is(bdi, span)[dir="ltr"]',
      '.order-date-invoice-item :is(bdi, span)[dir="ltr"]',
    ],
    ORDER_DATE: [
      '[data-component="orderDate"]',
      'span.order-date-invoice-item',
      '[data-component="briefOrderInfo"] div.a-column',
    ],
    ORDER_TOTAL: ['div.yohtmlc-order-total span.value', 'div.order-header div.a-col-left .a-span9'],
    ORDER_DETAIL_LINK: [
      'a.yohtmlc-order-details-link',
      'a[href*="order-details"]',
      'a[href*="orderID="]',
      'a[href*="orderId="]',
    ],
    ORDER_STATUS: [
      'div.yohtmlc-shipment-status-primaryText',
      '.od-status-message',
      'span.delivery-box__primary-text',
    ],
    ITEM_ROOT: [
      '[data-component="purchasedItems"] .a-fixed-left-grid',
      'div:has(> div.yohtmlc-item)',
      '.yohtmlc-item',
      '.item-box',
    ],
    ITEM_TITLE: [
      '[data-component="itemTitle"]',
      '.yohtmlc-item a',
      '.yohtmlc-product-title',
    ],
    ITEM_PRICE: [
      '[data-component="unitPrice"] .a-text-price :not(.a-offscreen)',
      '.yohtmlc-item .a-color-price',
    ],
    ITEM_QTY: ['.od-item-view-qty', 'span.item-view-qty', 'span.product-image__qty'],
    TRACKING_LINK: ['span.track-package-button a', 'a[href*="ship-track"]', 'a[href*="progress/tracker"]'],
    SHIPPING_ADDRESS: ['div.displayAddressDiv', '[data-component="shippingAddress"]'],
    ADDRESS_NAME: ['li.displayAddressFullName', 'span.displayAddressFullName'],
    ADDRESS_LINE1: ['li.displayAddressAddressLine1'],
    ADDRESS_LINE2: ['li.displayAddressAddressLine2'],
    ADDRESS_CITY: ['li.displayAddressCityStateOrRegionPostalCode'],
    ADDRESS_COUNTRY: ['li.displayAddressCountryName'],
    PAYMENT_LOGO: ['img.pmts-payment-credit-card-instrument-logo'],
    SUBTOTAL_ROWS: [
      '[data-component="orderSubtotals"] div.a-row',
      'div#od-subtotals div.a-row',
      '[data-component="chargeSummary"] div.od-line-item-row',
    ],
    NEXT_PAGE: [
      'ul.a-pagination li.a-last:not(.a-disabled) a',
      'a[aria-label*="Next"]',
      '.a-pagination li:last-child:not(.a-disabled) a',
    ],
    CANCELLED: ['div.yohtmlc-shipment-status-primaryText'],
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.OrderManagerAmazonSelectors = Selectors
  }
})()
