# TODO

- [ ] **Mobile layout** – Add responsive breakpoints, mobile-friendly nav (e.g. hamburger or stacked), and card/list layouts for tables on small screens so the app works well on phones.
- [ ] **Move orders filtering to backend** – Add query params (status, buying group, date range) to the orders API and filter on the server so the app scales when the database is large.
- [ ] **Pagination**: Add `limit`/`offset` (or cursor) to `list_orders`, payments list, shipments list, and `get_my_orders`; return something like `{ items, total_count, next_offset }` and have the frontend request pages.
- [ ] **Eager loading for orders list**: Use `joinedload` (or `selectinload`) in `list_orders` for `Order.store`, `Order.store_account`, `Order.buying_group`, `Order.items`, `Order.order_payments` (and `OrderPaymentMethod.payment_method`) so serializing to `OrderRead` doesn’t trigger N+1.
- [ ] **Indexes**: Add indexes on `orders(status)`, `orders(purchase_date)`, and `orders(buying_group_id)` (and composite if we often filter by status + date).
- [ ] **Frontend**: Request and render orders in pages (or virtualize the list) instead of loading the full list in one shot.