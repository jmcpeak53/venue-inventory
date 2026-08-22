# Slice 6 — Administrator booking operations and preparation lists

**Status:** Ready for implementation  
**Blocked by:** Slice 5

## Outcome

Give the venue administrator an efficient anonymous-booking work queue and a
printable preparation view without adding customer identities, statuses,
notifications, or fulfillment tracking.

## Scope

- Default the booking list to today and future, ordered by event date, with
  `Upcoming`, `Past`, and `All` filters.
- Search by non-secret booking reference.
- Add exact full-code lookup via a POST body and the existing code digest; never
  include the code in a URL, rendered value, or log.
- Show event date, reference, selected item-type count, total units, last
  updated, negative-remaining warning, and hidden-item warning.
- Let admin edit a booking to any past, present, or future event date without
  affecting customer access.
- Present booking detail with admin-editable quantities and a printer-friendly
  list containing reference, event date, items, quantities, and warnings.
- Add a `Print list` action using browser print CSS rather than generated files.
- Add a permanent booking deletion confirmation showing reference, event date,
  and selection count; cascade sessions and selections transactionally.

## Acceptance criteria

- [ ] Booking list defaults, date filters, ordering, and reference search match
      the defined Chicago calendar semantics.
- [ ] Exact code lookup finds one booking without exposing the code in browser
      history, proxy/application logs, or response HTML.
- [ ] Counts and warnings agree with booking-detail contents.
- [ ] Admin can freely change event date and customer login continues before
      and after that change.
- [ ] Admin basket edits follow admin authority and refresh revision/timestamp.
- [ ] Print output excludes navigation and controls while including all
      preparation fields and warnings.
- [ ] Booking deletion confirmation is explicit and successful deletion
      invalidates sessions and removes selections atomically.
- [ ] There is no status, customer name, contact field, audit history, or
      fulfillment checkbox.

## Testing approach

- Exercise filters, ordering, reference/code lookup, date edits, counts, and
  deletion through the HTTP interface against varied bookings.
- Capture logs and rendered URLs during exact-code lookup and assert the secret
  never appears.
- Use a browser smoke test for print media rendering and admin quantity edits.
- Test transaction rollback when session or selection cleanup fails.

## Out of scope

PDF/CSV export, notifications, user messaging, staff assignments, fulfillment
state, audit history, and code reset remain excluded.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at medium effort
- **OpenAI:** GPT-5.6 Terra at medium reasoning effort

## Top runtime failure points

1. Aggregate counts are calculated across bookings rather than within one
   booking. The admin list shows implausible totals; queries must group by the
   requested booking and be covered by multi-booking tests.
2. Exact-code search leaks through a query string or request logging. The code
   appears in history/log output; use POST, redaction, and explicit leak tests.
3. Print CSS hides item rows or warnings. The screen looks correct but printed
   output is incomplete; a browser media-emulation smoke test must assert the
   preparation content remains visible.

