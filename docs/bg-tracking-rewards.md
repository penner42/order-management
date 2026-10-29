# BG tracking rewards

BG scan rewards are reported once per tracking, including primary/alternate
identifiers. Orders shows the full amount beside the tracking number. When a
tracking spans orders, each order labels it **shared across orders**; these labels
are informational, rather than amounts to add together.

Receipt reconciliation still uses allocated line shares. The calculation is
`unit payout × quantity + allocated reward`: three $20 units with a $0.05 reward
are payable at $60.05. BG's reported amount is authoritative; it is not fixed at
five cents.

## Upgrade and repair

Run these commands in the deployed checkout, using that environment's normal
Compose configuration. Production is separate from this development checkout.

```sh
docker compose exec -T backend alembic upgrade head
docker compose exec -T backend python -m app.repair_tracking_rewards > bg-reward-preview.json
docker compose exec -T backend python -m app.repair_tracking_rewards --apply > bg-reward-apply.json
docker compose exec -T backend python -m app.repair_tracking_rewards > bg-reward-verification.json
```

The default mode previews changes and rolls them back. `--apply` commits the
tracking records, corrected line shares, and recalculated automatic pending
reviews. A second preview should report no tracking records created/updated,
line changes, or review updates. Existing unresolved evidence can remain in the
report on subsequent runs.

The repair uses the receipt cache only when its provider account matches the
current buying group. Existing receipt cash evidence determines which receipt
gets the reward. Missing/conflicting evidence and excess receipt quantities are
reported without changing the underlying receipt assignments, product prices,
quantities, or statuses. Cents for unmatched confirmed units remain reserved.

Manual payment reviews retain their choices and show validation conflicts.
Confirmed payments, their allocations, corrections, and bank confirmations are
preserved; the repair verifies their database snapshots before committing.
Reported payment discrepancies require review through the existing payment UI.

Keep the JSON reports with the deployment records. They contain local order,
receipt, and review identifiers, but no API credentials.
