# Venue Inventory proof-of-concept implementation plan

**Status:** Proposed — ready for operator approval  
**Prepared:** 2026-08-22  
**Implementation authorization:** None. This document does not authorize
application, DNS, or VPS changes.

## Outcome

Replace the static hello-world container with a lightweight, mobile-friendly
inventory application at `https://inventory.needleminder.app`.

The venue administrator will manage décor inventory and anonymous booking
records. A booking party will enter a generated access code, browse the full
catalog, and maintain a continuously saved selection basket. The application
will deliberately avoid scheduling, cross-booking availability, checkout,
payments, messaging, and unnecessary personal data.

## Product assessment

This is a good proof-of-concept candidate because the valuable workflow is
small: publish a visual catalog, give each party private access, and let the
vendor inspect a preparation list. SQLite is appropriate for the expected
single-venue workload when writes are short, WAL mode is enabled, and lock
contention is handled explicitly.

The main product risk is terminology. These are **selections**, not guaranteed
reservations against shared availability. The interface must never imply that
an item selected by one party is unavailable to another party. Every booking
gets an independent view of the catalog quantity.

## Agreed scope

### Administrator

- One shared administrator password; no staff-account CRUD.
- Log in through a protected admin page.
- Create, view, edit, hide, and delete catalog items under the deletion rules
  below.
- Add, replace, or remove an optional item image.
- Create anonymous bookings and receive a generated access code exactly once.
- View bookings by upcoming, past, or all dates.
- Search bookings by non-secret reference or exact access code.
- Edit any booking date freely, including moving it into the past or future.
- Inspect and modify every booking basket.
- Permanently delete any booking after an explicit confirmation.
- Print a simple preparation list from a booking detail page.

### Booking party

- Enter a generated code on the access page.
- Browse visible inventory in a responsive card grid.
- Search item names and descriptions.
- Switch between all items and the current basket.
- Select quantities and receive immediate saved/error feedback.
- Return later and continue changing the same live basket.
- See negative remaining quantities when the catalog quantity was reduced
  below an existing selection.
- See previously selected hidden items, but not change their locked quantities.
- Log out explicitly.

### Privacy boundary

A booking stores no name, email address, phone number, notes, or status. It
contains only an internal ID, a non-secret reference, an access-code digest,
an event date, timestamps, and item selections. Access codes must never appear
in URLs or normal application logs.

## Explicitly out of scope

- Scheduling or prevention of same-day bookings
- Shared availability or reconciliation between booking parties
- Checkout, submission, approval, or finalized basket states
- Pricing, payments, contracts, deposits, or invoicing
- Names, contact information, free-form booking notes, or other PII
- Multiple administrator accounts, roles, or password-management screens
- Access-code recovery, reset, or regeneration
- Customer access expiry based on event date
- Categories, tags, SKUs, colors, dimensions, or multiple images per item
- Email or SMS sending
- Notifications, detailed audit history, or change attribution
- Fulfillment checklists or item-return tracking
- PDF generation or CSV export
- Offsite backups in this phase

## Business rules

### Inventory quantities

1. `stock_quantity` is a nonnegative integer set by the administrator.
2. A visible item can initially be selected by a customer from zero through
   its current `stock_quantity`.
3. Other bookings never affect that range.
4. Per-booking remaining quantity is calculated as:

   ```text
   current catalog quantity - this booking's selected quantity
   ```

5. If the administrator later lowers stock below an existing selection, keep
   the selection and show the negative result to both customer and admin.
6. Never reconcile baskets, automatically reduce a selection, or block a
   catalog edit because of a negative result.
7. The administrator can set any basket quantity to any nonnegative integer,
   including a value above catalog stock.

### Visibility and deletion

- A hidden, unselected item is absent from the customer catalog.
- A hidden item already in a basket remains visible in that basket with an
  `Unavailable` label and a read-only quantity.
- Showing the item again makes the customer quantity editable again.
- The administrator can always edit or remove a hidden-item selection.
- Permanent item deletion is blocked if any booking selecting it has an event
  date equal to or later than today in `America/Chicago`.
- Permanent item deletion is allowed if there are no selections or every
  selecting booking is in the past. Deletion then removes the past selection
  rows and the stored image.
- Today remains current for the whole Chicago calendar day.

### Bookings and access codes

- A booking date is required but informational; it does not reserve shared
  stock or disable customer access.
- Each booking receives an immutable, non-secret reference such as `B-0042`.
- Generate 12 random, case-insensitive characters from an alphabet excluding
  ambiguous characters, displayed as `XXXX-XXXX-XXXX`.
- Show the access code only on the booking-creation success screen, with a copy
  button and an explicit warning that it cannot be retrieved later.
- Store a keyed HMAC-SHA-256 digest of the normalized code, not plaintext. The
  secret HMAC key lives only in the VPS environment file.
- If the code is lost, the supported workflow is to delete the booking and
  create a new empty one. There is no basket transfer.
- Codes remain valid until their bookings are deleted.
- Deleting a booking removes its sessions and selections in the same database
  transaction.

## Catalog item model

| Field | Rule |
|---|---|
| ID | Internal integer primary key |
| Name | Required, trimmed, maximum length enforced |
| Description | Optional short plain text with a maximum length |
| Stock quantity | Required nonnegative integer |
| Image filename | Optional generated filename; never accept a user path |
| Visible | Required boolean, default true |
| Created/updated timestamps | Stored in UTC |

There is one optional primary image per item. A neutral placeholder is shown
when it is absent.

## Application architecture

Use one server-rendered Python service:

```text
Browser
  -> Caddy (DNS, HTTPS, security headers)
  -> Flask/Gunicorn container
       -> SQLite database in /data
       -> normalized images in /data/images
```

Recommended components:

- Flask application factory and blueprints for public, customer, and admin
  routes
- Jinja templates and ordinary CSS
- Small, framework-free JavaScript modules for basket autosave, saved/error
  state, image preview, and clipboard copy
- SQLAlchemy models and Alembic migrations
- Pillow for image validation and normalization
- Gunicorn as the container process
- Server-side opaque sessions persisted in SQLite
- CSRF protection on every state-changing form and JSON request
- Login throttling with generic failure responses
- Locked Python dependencies and a reproducible container image

Configure SQLite with foreign keys, WAL journal mode, a busy timeout, and
short explicit transactions. Use one Gunicorn worker with a small thread pool
for this low-volume prototype. Basket writes update only one item at a time.

## Data model

### `inventory_items`

- `id`
- `name`
- `description` nullable
- `stock_quantity`
- `image_filename` nullable
- `is_visible`
- `created_at`
- `updated_at`

### `bookings`

- `id`
- `public_reference` unique
- `access_code_digest` unique
- `event_date`
- `revision` for optimistic concurrency protection
- `created_at`
- `updated_at`

### `booking_selections`

- `booking_id` foreign key with cascade on booking deletion
- `inventory_item_id` foreign key with restricted deletion except through the
  explicit past-booking deletion service
- `selected_quantity` positive integer
- composite unique key on booking and item
- `updated_at`

Representing zero removes the selection row.

### `web_sessions`

- Opaque random session identifier digest
- Actor type (`admin` or `booking`)
- Nullable booking foreign key with cascade deletion
- Created, last-seen, and expiry timestamps

Customer sessions last 30 days. Admin sessions last 12 hours. Session cookies
are `Secure`, `HttpOnly`, and `SameSite=Lax`. Only an opaque identifier is sent
to the browser.

## Screens and routes

### Public/customer

1. Access-code entry page
2. Customer catalog with search and `All items / My basket` toggle
3. Responsive cards with optional image, name, description, catalog quantity,
   selected quantity, and per-booking remaining quantity
4. Persistent basket summary with selected item-type and total-unit counts
5. Logout action

Autosave requests are serialized per item so older responses cannot overwrite
newer input. The server rechecks visibility and quantity rules on every write.
Use the booking revision to reject stale writes, refresh current state, and
show a clear retry message instead of silently overwriting a newer admin edit.

### Administrator

1. Admin login/logout
2. Dashboard with links to inventory and bookings
3. Inventory list with text search and visible/hidden filter
4. Create/edit item form with optional image controls
5. Item deletion confirmation or an explanation of blocking future selections
6. Booking list defaulting to today/future, with `Upcoming / Past / All`
   filters, reference search, exact-code lookup, counts, last-updated time, and
   warnings
7. Booking creation form requiring only an event date
8. One-time code display with copy control
9. Booking detail with freely editable date and administrator-editable basket
10. Printer-friendly preparation view using browser print support
11. Booking deletion confirmation showing reference, date, and selection count

Exact-code lookup must use a POST body so secrets do not enter URLs, browser
history, analytics, or proxy access logs.

## Image pipeline

1. Accept JPEG, PNG, or WebP files up to 10 MB.
2. Verify actual decoded image content rather than trusting extension or MIME
   type.
3. Reject malformed files and decompression-bomb dimensions.
4. Correct EXIF orientation.
5. Fit within 1600 by 1600 pixels without enlargement.
6. Convert to WebP and discard EXIF and other metadata.
7. Write to a temporary file, fsync, and atomically rename to a random
   application-generated filename.
8. Commit the database change before deleting the replaced image.
9. On failure, preserve the prior database row and image and clean up the
   temporary file.

## Security and privacy controls

- Terminate TLS at Caddy and redirect HTTP to HTTPS.
- Store the administrator Argon2id password hash, access-code HMAC secret,
  Flask secret material, and session secret in a root-readable VPS environment
  file that is never committed.
- Use generic login errors and rate-limit failed customer and administrator
  attempts by trusted client IP.
- Configure Flask to trust proxy headers only from the Caddy network.
- Apply CSRF protection, secure-cookie settings, content security policy,
  frame denial, MIME sniffing prevention, and a restrictive referrer policy.
- Use POST for logout and destructive actions.
- Escape all catalog content in templates; descriptions remain plain text.
- Redact access-code bodies, cookies, and credentials from logs.
- Send `noindex, nofollow, noarchive` directives, while making clear that this
  is not an access-control mechanism.
- Run the application as a non-root container user and limit writes to the
  persistent data directory and temporary upload directory.

## Backup and restore

- Persist `/data/venue-inventory.sqlite3` and `/data/images` in a dedicated VPS
  directory or named volume that survives image and container replacement.
- Use the SQLite online backup API, not a raw copy of a live database.
- Coordinate database and image capture with an application backup lock.
- Create a backup automatically before every deployment.
- Run a nightly server timer and retain backups for 14 days.
- Include the database, images, a manifest, checksums, and deployed Git SHA.
- Write a restore script that stops application writes, verifies checksums,
  restores both database and images, reapplies safe permissions, starts the
  application, and runs health and smoke checks.
- Exercise one restore into an isolated temporary Compose project before the
  proof of concept is accepted.
- Document that same-VPS backups do not protect against total disk or VPS loss.

## CI and coding-agent deployment

The operator does not run terminal commands. A coding agent owns the complete
technical workflow after being told to implement or deploy.

### Continuous integration

Add GitHub Actions checks for every pull request and push:

- Dependency installation from the lockfile
- Formatting and lint checks
- Static/type checks where configured
- Unit and integration tests
- Alembic upgrade from an empty database and schema-head verification
- Container build
- A small browser smoke test covering admin login, code login, and basket save

### Deployment

Extend the existing deployment script to:

1. Confirm the target Git SHA is pushed and its required CI checks passed.
2. SSH through the existing `prod-vps-01` profile.
3. Refuse a dirty or divergent VPS checkout.
4. Record the prior healthy Git SHA and create a verified pre-deployment
   backup.
5. Fetch the target revision and build its image before replacing the running
   container.
6. Apply Alembic migrations using the same image.
7. Start the service and wait for database-aware readiness.
8. Verify the container health endpoint and the public HTTPS smoke path.
9. If migration, startup, or health verification fails, restore the previous
   Git revision and corresponding backup, restart it, and verify rollback.
10. Report the deployed SHA, backup path, migration revision, and public URL.

The deployment script, backup timer, and restore tooling must be idempotent and
fully documented for future coding agents. No routine step is delegated to the
operator.

## HTTPS and VPS integration

The current authoritative nameservers are Porkbun and `needleminder.app`
already resolves to `5.78.222.116`. During implementation, the coding agent
will:

1. Create `inventory.needleminder.app` DNS records through the provider API or
   an authenticated browser session if available.
2. Join the Venue Inventory container to the existing Caddy Docker network
   using an external Compose network; do not merge Compose projects.
3. Add only the isolated `inventory.needleminder.app` reverse-proxy block to
   `/apps/needle-minder/Caddyfile` after creating a timestamped backup.
4. Validate the complete Caddy configuration before reload.
5. Reload Caddy without interrupting `needleminder.app`.
6. Verify both the existing Needleminder site and the new inventory site.
7. Remove the public TCP 8080 firewall rule and stop publishing the application
   port after HTTPS works. Caddy becomes the only public ingress.

If no authenticated Porkbun access is available to the coding agent, DNS is
the only external dependency. The agent must report that specific blocker and
request access; the operator will not be asked to run server commands.

## Implementation sequence

Keep each phase independently reviewable and green in CI.

### Phase 1 — Application and persistence foundation

- Replace the static Nginx-only runtime with the Flask/Gunicorn scaffold.
- Add locked dependencies, configuration validation, structured logging,
  SQLite connection settings, models, initial migration, persistent volumes,
  and liveness/readiness endpoints.
- Add the test harness and CI workflow.

### Phase 2 — Authentication and sessions

- Add administrator password verification and 12-hour server-side sessions.
- Add booking code generation/digests, rate-limited customer login, 30-day
  sessions, logout, CSRF protection, and security headers.
- Test that plaintext codes never persist or enter URLs/logs.

### Phase 3 — Catalog administration and images

- Build inventory CRUD, search, visibility controls, optional image handling,
  atomic replacement, placeholders, and deletion guardrails.
- Cover current/past date deletion rules at the Chicago date boundary.

### Phase 4 — Booking administration

- Build booking creation and one-time code display.
- Build booking filters/search, exact-code lookup, warnings, date editing,
  basket administration, hard deletion, and print styles.

### Phase 5 — Customer catalog and live basket

- Build responsive catalog/search/basket screens.
- Implement item-scoped autosave, saved/error state, optimistic conflict
  handling, negative remaining values, and hidden-item locking.
- Test independent inventory behavior across multiple bookings.

### Phase 6 — Operations and launch

- Implement backup, retention, restore, migration, CI gating, and rollback.
- Configure persistent production secrets and generate the initial admin
  password for one-time handoff to the operator.
- Add DNS and Caddy routing, close port 8080, deploy, verify both sites, and run
  the isolated restore drill.
- Update `README.md` and `docs/deployment.md` with the final setup, commands,
  repository structure, feature set, and agent/operator runbooks.

## Test matrix

At minimum, automated tests must prove:

- Correct and incorrect admin passwords; admin session expiry and logout
- Code generation alphabet/length, normalization, uniqueness, digest-only
  storage, lookup, throttling, session expiry, and deletion invalidation
- Booking references are unique and non-authenticating
- Two bookings each see the full catalog quantity regardless of the other's
  basket
- Customers cannot initially select above stock
- Admin stock reductions preserve selections and produce negative remaining
- Hidden selected items are visible but customer-locked; admin edits still work
- Item deletion blocks current/future selections and permits past-only cascades
  using `America/Chicago`, including today and daylight-saving boundaries
- Booking dates accept past, present, and future edits without changing access
- Booking deletion atomically removes selections and sessions
- Basket autosave is ordered, reports failure, and rejects stale revisions
- Images are optional and can be added, replaced, removed, and rolled back on
  failure; invalid, oversized, or metadata-bearing inputs are handled safely
- CSRF, secure-cookie, authorization, and role-boundary checks protect every
  state-changing endpoint
- Empty-database migrations reach the expected head
- Backup checksums and an isolated restore reproduce database rows and images
- Container readiness fails when persistent data is unavailable
- Public HTTPS works while the existing Needleminder site remains unchanged

## Acceptance criteria

- [ ] A customer can use a generated code to enter, search the catalog, create
      a basket, leave, return, and see the saved basket.
- [ ] A second customer starts from the same full item quantities regardless
      of the first customer's selections.
- [ ] Admin inventory CRUD, visibility, optional image lifecycle, and deletion
      constraints match the agreed rules.
- [ ] Admin booking creation, anonymous identification, filters, date editing,
      basket editing, warnings, printing, and deletion work end to end.
- [ ] No customer PII or plaintext access code is stored.
- [ ] HTTPS, session security, CSRF, rate limiting, upload validation, and log
      redaction pass automated checks.
- [ ] SQLite data and images survive rebuilds and container replacement.
- [ ] Pre-deployment and nightly backups run automatically, retain 14 days,
      and pass an isolated restore drill.
- [ ] CI gates deployment and a failed release automatically returns to the
      prior healthy revision.
- [ ] `inventory.needleminder.app` works over HTTPS, raw port 8080 is closed,
      and `needleminder.app` remains healthy.
- [ ] The operator receives the URL and one-time admin credential without
      needing to run terminal, Git, Docker, DNS, or VPS commands.
- [ ] README and deployment documentation match the delivered system.

## Three most likely runtime failure points

These risks must be addressed before implementation approval.

1. **SQLite write contention during autosave.** A customer changes quantities
   while the admin edits the same booking or image metadata, and a write waits
   too long or returns `database is locked`. The visible symptom is a basket
   stuck on `Saving` or showing a retry error while reads still work. Mitigate
   with WAL, a busy timeout, short item-scoped transactions, serialized client
   writes, bounded server retries, and explicit failure UI.
2. **Persistent data directory unavailable, read-only, or full.** The container
   starts under the wrong UID, a volume is omitted, or the VPS disk fills. The
   visible symptom is readiness failure, HTTP 503, image-upload errors, or—if
   the mount were accidentally omitted—an apparently empty catalog after a
   redeploy. Mitigate with startup ownership/free-space checks, a fixed
   production mount, readiness checks that exercise the database and image
   directory, automatic backups, and a restore drill.
3. **DNS/Caddy/container-network misconfiguration.** The subdomain is missing,
   Caddy cannot resolve the separate application container, or certificate
   issuance fails. The visible symptom is DNS failure, a browser certificate
   warning, or Caddy `502 Bad Gateway`, while the original Needleminder site may
   still work. Mitigate with staged DNS validation, a shared external Docker
   network, Caddy config validation before reload, a timestamped config backup,
   and smoke tests for both hostnames before port 8080 is closed.

## Recommended implementation models

- **Anthropic:** Claude Sonnet 5 at high effort. Anthropic describes Sonnet 5
  as its best speed/intelligence combination; this is sufficient for a bounded
  Flask application while avoiding the cost of Opus for every phase. Use
  Claude Opus 5 for a final security/deployment review if available.
- **OpenAI:** GPT-5.6 Terra at high reasoning effort. OpenAI positions Terra as
  the GPT-5.6 balance of intelligence and cost, which fits this multi-phase but
  conventional web application. Use GPT-5.6 Sol for the final security and
  rollback review if available.

Current model references:

- [Anthropic models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [OpenAI model catalog](https://developers.openai.com/api/docs/models/all)

## Approval gate

After the operator approves this plan, implementation may begin with Phase 1.
No DNS, Caddy, VPS, or application changes should occur before that approval.
