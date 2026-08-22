# Slice 4 — Independent live customer baskets

**Status:** Ready for implementation  
**Blocked by:** Slice 3

## Outcome

Give an authenticated booking party a responsive, searchable catalog and a
continuously saved basket, while allowing the administrator to inspect and edit
the same selections. Every booking evaluates stock independently.

## Scope

- Add booking-selection rows keyed by booking and inventory item, with positive
  selected quantity and timestamps; zero removes the row.
- Add the customer catalog card grid with optional image/placeholder, name,
  description, stock, selected quantity, and per-booking remaining quantity.
- Add name/description search, `All items / My basket` toggle, and basket totals
  for selected item types and units.
- Let customers select from zero through current stock without considering any
  other booking.
- Autosave item-scoped changes, serialize requests per item, and display
  `Saving`, `Saved`, and actionable retry states.
- Use booking revisions to reject stale requests, refresh current state, and
  prevent an old browser response from overwriting a newer admin edit.
- Add admin booking detail that lists and edits every visible selection. Admin
  may set any nonnegative quantity, including above stock.
- Update the booking's last-updated timestamp for either actor's basket change.

## Acceptance criteria

- [ ] An authenticated customer can browse/search visible items and switch to
      a basket-only view on mobile and desktop.
- [ ] Quantity changes persist without a save, checkout, or finalize action and
      show accurate network state.
- [ ] Two bookings each select up to the same full catalog stock regardless of
      the other's selections.
- [ ] Customer increases above current stock are rejected server-side; zero
      removes the selection.
- [ ] Admin can inspect and set any basket quantity immediately.
- [ ] A stale customer write does not silently overwrite a newer accepted edit.
- [ ] Refreshing or returning in a later session shows the saved basket.
- [ ] Booking list/detail last-updated time changes after either actor edits the
      basket.
- [ ] Keyboard, screen-reader labels, focus, and error announcements work for
      quantity and autosave controls.

## Testing approach

- Use HTTP integration tests with two bookings to prove independent stock and
  admin/customer authorization boundaries.
- Use a real-browser smoke test to exercise rapid quantity changes, ordering,
  visible saved/error states, refresh persistence, and responsive controls.
- Inject stale revisions and transient server failures and assert explicit
  refresh/retry rather than silent data loss.
- Verify aggregate counts from persisted selection rows through rendered UI.

## Out of scope

Cross-booking aggregation, availability reconciliation, hidden-item locking,
negative stock after catalog edits, booking filters, and printing are deferred.

## Suggested implementation models

- **Anthropic:** Claude Opus 5 at high effort
- **OpenAI:** GPT-5.6 Sol at high reasoning effort

## Top runtime failure points

1. Rapid autosave requests complete out of order. The quantity visibly jumps
   backward after showing `Saved`; per-item serialization and revision checks
   must reject that stale response.
2. SQLite write contention occurs between customer autosave and admin editing.
   The UI remains on `Saving` then shows retry rather than losing the intended
   value; WAL, busy timeout, and short transactions bound the failure.
3. Client-side limits are bypassed. A crafted request saves above-stock
   customer quantity unless the server independently enforces the current item
   rule in the same transaction.
