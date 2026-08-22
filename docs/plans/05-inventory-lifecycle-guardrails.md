# Slice 5 — Inventory lifecycle guardrails

**Status:** Ready for implementation  
**Blocked by:** Slice 4

## Outcome

Make catalog reductions, visibility changes, and deletion safe and explicit
without introducing scheduling or cross-booking reconciliation. Customers keep
existing selections; administrators see operational warnings and retain full
control.

## Scope

- Preserve existing selections when admin lowers stock and compute remaining as
  current stock minus only that booking's selected quantity, including negative
  values.
- Show negative-remaining warnings in affected customer baskets, admin booking
  details, and the booking list.
- Remove hidden items from general customer browsing.
- Retain a hidden item already selected by that booking in `My basket`, mark it
  unavailable, and lock its customer quantity.
- Restore normal customer editability if the item becomes visible again.
- Keep hidden-item selection fully editable by admin.
- Block permanent item deletion when any selecting booking has an event date on
  or after today in `America/Chicago`.
- Allow deletion with no selections or past-only selections; transactionally
  remove those selection rows and the item, then safely clean its image.
- Present the blocking booking references/dates and recommend hiding rather than
  deleting; expose no PII or codes.

## Acceptance criteria

- [ ] Admin stock reduction never changes a saved selection and can produce a
      clearly labeled negative remaining number.
- [ ] Other bookings remain irrelevant to every calculation and warning.
- [ ] A hidden unselected item disappears from a customer's catalog.
- [ ] A hidden selected item remains in that customer's basket with locked
      quantity and an unavailable label.
- [ ] Admin can change or remove that hidden selection, and showing the item
      again unlocks customer changes.
- [ ] Item deletion is blocked by any selection dated today or later in Chicago
      and identifies the non-secret blocking references.
- [ ] Item deletion succeeds with only past selections and removes those rows
      and the image without deleting their bookings.
- [ ] The event date remains freely editable and never disables code access.

## Testing approach

- Freeze Chicago time around midnight and daylight-saving transitions and
  exercise deletion through admin HTTP behavior.
- Use several bookings across past/today/future dates to prove the guard checks
  all references and ignores unrelated baskets.
- Use browser tests for hidden-item visibility/locking and negative warning
  presentation.
- Inject image-cleanup failure after deletion and assert data integrity plus
  recoverable orphan logging.

## Out of scope

Shared availability, conflict resolution, automatic quantity correction,
customer notifications, scheduling, and event-date access expiry remain out.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at high effort
- **OpenAI:** GPT-5.6 Terra at high reasoning effort

## Top runtime failure points

1. Server and browser use different dates or timezones. An item is deletable a
   day early/late; all eligibility must derive from the injected Chicago date.
2. A hidden-item rule is enforced only in the UI. A stale or crafted customer
   request changes the locked quantity unless the transaction rechecks current
   visibility.
3. Item and past selections delete but image cleanup raises afterward. The
   request must still report the committed outcome and log a safe orphan rather
   than rolling the database into a misleading partial state.

