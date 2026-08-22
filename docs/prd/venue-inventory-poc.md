# Venue Inventory proof of concept

## Problem Statement

A venue owns reusable décor such as signs, candles, and artificial flowers,
but does not have a simple visual system through which a booking party can
record what they would like the venue to prepare. General inventory and event
platforms introduce scheduling, global allocation, customer accounts, or
other complexity that the venue does not need for an early proof of concept.

The venue needs a lightweight catalog and preparation-list workflow. It must
avoid unnecessary personal information, remain understandable on a phone, and
let the vendor retain control when inventory or booking details change.

## Solution

Build a self-hosted, server-rendered inventory application at
`inventory.needleminder.app`. A venue administrator creates anonymous booking
records containing only an event date, receives a one-time access code, and
sends that code to the booking party outside the application. The party uses
the code to browse a visual catalog and maintain an automatically saved basket.

Catalog quantity is evaluated independently for each booking. The application
does not schedule events or reconcile inventory across parties. The vendor can
inspect and edit every basket, identify anonymous bookings using a non-secret
reference, and print a preparation list.

## User Stories

1. As a venue administrator, I want one protected admin login, so that catalog and booking controls are not public.
2. As a venue administrator, I want the shared password managed outside the UI, so that the prototype does not need account administration.
3. As a venue administrator, I want to create an item with a name and quantity, so that it appears in the décor catalog.
4. As a venue administrator, I want item descriptions to be optional, so that sparse records remain valid.
5. As a venue administrator, I want item images to be optional, so that I can publish an item before photography is available.
6. As a venue administrator, I want to add, replace, or remove an image later, so that the catalog can improve over time.
7. As a venue administrator, I want to hide an item without erasing existing selections, so that unavailable décor is not offered to new customers.
8. As a venue administrator, I want safe item deletion rules, so that current and future preparation lists are not silently damaged.
9. As a venue administrator, I want to delete items selected only by past bookings, so that obsolete inventory can eventually be removed.
10. As a venue administrator, I want to create a booking using only an event date, so that the application stores no unnecessary PII.
11. As a venue administrator, I want an automatic non-secret booking reference, so that anonymous bookings remain distinguishable.
12. As a venue administrator, I want a strong customer access code shown once, so that I can send it without the application storing readable credentials.
13. As a venue administrator, I want exact access-code lookup, so that a code recovered from vendor email can locate its booking.
14. As a venue administrator, I want to edit booking dates freely, so that reconciliation remains the vendor's responsibility.
15. As a venue administrator, I want upcoming, past, and all-booking filters, so that preparation work is easy to prioritize.
16. As a venue administrator, I want basket counts and last-updated times in the booking list, so that I can assess activity quickly.
17. As a venue administrator, I want warnings for hidden items and negative remaining quantities, so that exceptions are visible.
18. As a venue administrator, I want to edit any booking basket, so that I can make operational corrections.
19. As a venue administrator, I want a printer-friendly preparation list, so that staff can gather items away from a screen.
20. As a venue administrator, I want to delete a booking and its basket permanently, so that a lost-code booking can be replaced cleanly.
21. As a booking party, I want to enter a human-readable code, so that I can access my selection list without an account.
22. As a booking party, I want my session to persist, so that I do not repeatedly enter the code.
23. As a booking party, I want a responsive visual catalog, so that I can browse comfortably on a phone.
24. As a booking party, I want to search names and descriptions, so that I can find an item quickly.
25. As a booking party, I want to switch between the full catalog and my basket, so that I can review my current choices.
26. As a booking party, I want each quantity change saved automatically, so that there is no checkout or submission step.
27. As a booking party, I want clear saved and retry feedback, so that I know whether a change persisted.
28. As a booking party, I want my available quantity calculated independently, so that other parties never block my choices.
29. As a booking party, I want an existing overage preserved after an admin stock reduction, so that my selection is not changed silently.
30. As a booking party, I want a previously selected hidden item displayed but locked, so that I can see the vendor-controlled change.
31. As a booking party, I want access to remain open after the event date, so that date alone does not invalidate my code.
32. As the operator, I want deploys, migrations, backups, health checks, and rollback automated, so that I do not run terminal commands.
33. As the operator, I want the application isolated from the Needleminder source and container, so that development does not destabilize the existing site.
34. As the operator, I want HTTPS and secure sessions, so that admin and booking credentials do not cross the network in plaintext.
35. As the operator, I want nightly and pre-deployment backups with a tested restore, so that a failed release is recoverable.

## Implementation Decisions

- Use a server-rendered Flask application with Jinja, modest framework-free
  JavaScript, SQLAlchemy, Alembic, Pillow, and Gunicorn.
- Use SQLite with foreign keys, WAL mode, a busy timeout, short transactions,
  and a persistent data directory shared with normalized images.
- Store catalog items with required name and nonnegative stock quantity,
  optional description and image, visibility, and timestamps.
- Store bookings with an internal ID, immutable non-secret reference, keyed
  access-code digest, freely editable event date, revision, and timestamps.
- Generate 12 case-insensitive characters excluding ambiguous characters and
  display them as `XXXX-XXXX-XXXX` exactly once.
- Use a keyed HMAC digest for indexed code lookup; never persist plaintext
  access codes or place them in URLs.
- Store opaque server-side sessions. Customer sessions last 30 days and admin
  sessions last 12 hours.
- Treat catalog stock independently per booking. Customer selection is capped
  by current stock only when first increased; later admin reductions preserve
  the selection and may produce a negative remaining result.
- Hide selected items from general browsing but retain them as read-only rows
  in affected customer baskets. Administrators retain full edit authority.
- Determine past-item deletion eligibility using `America/Chicago`; today is
  current until the following local calendar day.
- Normalize optional JPEG, PNG, or WebP uploads to metadata-free WebP within
  1600 by 1600 pixels and persist them atomically.
- Terminate HTTPS through the existing VPS Caddy instance while keeping the
  application repository, container, database, and Compose project separate.
- Keep the application container off public port 8080 after HTTPS is live.
- Gate deployment on CI, create pre-deployment backups, apply migrations,
  verify readiness and the public route, and roll back automatically on
  failure.

## Testing Decisions

Tests assert externally observable behavior rather than ORM or helper
implementation details.

- Use Flask's HTTP test client against a temporary SQLite database and image
  directory as the primary seam for admin, customer, authorization, CRUD,
  deletion, date, and quantity behavior.
- Use a small real-browser smoke suite at the highest UI seam for JavaScript
  autosave ordering, saved/error states, session cookies, image presentation,
  and the printable preparation view.
- Exercise the real image decoder and filesystem for upload normalization,
  replacement, metadata stripping, and failure rollback.
- Exercise Alembic against an empty database and validate the resulting schema
  head.
- Exercise backup and restore through their public commands against an
  isolated data directory, verifying rows, images, checksums, and Git metadata.
- Exercise the built container's liveness/readiness endpoints and deployment
  script behavior with command-level tests; reserve DNS/Caddy and public HTTPS
  checks for the launch slice.
- The current repository is only a static scaffold, so no existing application
  test seam can be reused. The first vertical slice establishes these seams for
  every later issue.

## Out of Scope

- Scheduling, same-day conflict checks, or shared availability
- Checkout, finalization, approval, or submission state
- Names, emails, phone numbers, notes, or other customer PII
- Multiple administrators, roles, or password-management screens
- Access-code recovery, reset, regeneration, or automatic expiry
- Categories, tags, SKUs, pricing, dimensions, or multiple images
- Application-sent email or SMS
- Notifications, audit history, attribution, or fulfillment state
- PDF/CSV generation, payments, contracts, invoicing, and returns
- Offsite backups during the proof of concept

## Further Notes

The authoritative detailed product plan is
`docs/plans/venue-inventory-poc.md`. Implementation is divided into separate
Slipstream-ready vertical-slice issues, each with its own committed plan and
explicit dependency marker. The parent PRD is planning context and must not be
bootstrapped as an implementation issue.
