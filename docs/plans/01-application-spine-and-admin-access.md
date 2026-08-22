# Slice 1 — Application spine and administrator access

**Status:** Ready for implementation  
**Blocked by:** None

## Outcome

Replace the static hello-world runtime with a deployable Flask application
that has durable SQLite persistence, a verified container health contract,
continuous integration, and one protected administrator dashboard.

This slice must be demoable by starting the container, signing in with the
configured shared admin password, viewing the empty dashboard, signing out,
and observing healthy readiness.

## Scope

- Establish the Flask application factory, configuration validation, Jinja
  layout, CSS baseline, Gunicorn entrypoint, and production container.
- Add locked dependencies and a single checked-in verification script that
  runs formatting, linting, tests, and migration checks inside Docker.
- Add SQLAlchemy, Alembic, an initial schema, foreign-key enforcement, WAL,
  busy timeout, and a persistent `/data` directory.
- Add opaque server-side sessions with secure cookie settings.
- Verify a shared administrator password against an Argon2id hash supplied by
  the environment; never store the password or hash in SQLite.
- Provide admin login, empty dashboard, logout, 12-hour expiry, generic failure
  responses, CSRF protection, and rate limiting.
- Provide `/healthz` liveness and database/filesystem-aware `/readyz`.
- Add GitHub Actions using the same verification entrypoint as Slipstream.
- Preserve responsive and accessible HTML fundamentals from the first screen.

## Acceptance criteria

- [ ] `docker compose up --build --wait` starts a non-root Flask/Gunicorn
      container and reports healthy.
- [ ] SQLite and session data live in the configured persistent data mount and
      survive container replacement.
- [ ] Correct admin credentials create a 12-hour opaque session; incorrect or
      throttled attempts reveal no credential details.
- [ ] Unauthenticated requests cannot open the admin dashboard.
- [ ] Logout invalidates the server-side session and uses a CSRF-protected POST.
- [ ] Liveness works without touching dependencies; readiness fails when the
      database or writable data directory is unavailable.
- [ ] A migration upgrades an empty database to head.
- [ ] CI and `scripts/verify.sh` run the same deterministic checks successfully.
- [ ] The README documents the new setup, running, testing, and project shape.

## Testing approach

- Test admin login, expiry, logout, CSRF, throttling, and authorization through
  Flask's HTTP client with a temporary data directory.
- Test configuration refusal when required secrets or paths are absent.
- Run Alembic from an empty database and compare with declared metadata.
- Build the real image and probe liveness/readiness from Compose.
- Keep tests deterministic by injecting time and rate-limit storage.

## Out of scope

Catalog items, booking codes, customer access, baskets, image uploads, backup,
DNS, Caddy, and production deployment remain for later slices.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at high effort
- **OpenAI:** GPT-5.6 Terra at high reasoning effort

## Top runtime failure points

1. The production data mount is missing or owned by the wrong UID. Readiness
   returns 503 and login/session writes fail instead of silently using the
   container filesystem.
2. The admin hash or session secret is absent or malformed. The container
   refuses startup with a configuration error rather than exposing an
   unprotected dashboard or issuing unstable sessions.
3. SQLite initialization or migration does not reach head. Readiness remains
   unhealthy and the dashboard is unavailable, with the migration revision in
   structured logs.

