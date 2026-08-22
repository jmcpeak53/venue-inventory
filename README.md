# Venue Inventory

A self-hosted web app for managing venue inventory. This slice is a Flask
application with SQLite persistence, a protected administrator catalog, and
container health checks.

## Status

The static hello-world prototype has been replaced by a Flask/Gunicorn
container. You can start it, sign in with the shared administrator password,
create and search catalog items, add optional normalized images, sign out, and
confirm that readiness is healthy. Bookings and HTTPS come in later slices.

## Setup

Requirements: Docker with Docker Compose.

The repository includes local development defaults so the container can start
without extra files:

- Administrator password: `local-admin-password`
- Session cookies are not marked `Secure` (this machine is serving HTTP)
- SQLite, session rows, and normalized catalog images are stored in the
  `venue-inventory-data` Docker volume

Those defaults are only for local use. Before any shared installation, copy
`.env.example` to `.env` and replace the secret key and password hash.

To create a new administrator password hash:

1. Start from this repository folder in a terminal.
2. Run:

   ```bash
   docker compose run --rm --no-deps --entrypoint python web -m app.hash_password
   ```

3. Type the password twice. The command prints the Argon2id hash and a
   Compose-ready `.env` line with every `$` already doubled.
4. Copy that `VENUE_INVENTORY_ADMIN_PASSWORD_HASH=...` line into `.env`.
   Docker Compose treats `$` as a variable. If you paste the raw hash
   instead of the Compose-ready line, double every `$`
   (`$argon2id$v=19$...` becomes `$$argon2id$$v=19$$...`). Pasting the
   hash unchanged makes Compose mangle it and startup fails with "must
   be an Argon2id encoded hash".

   To skip `$` doubling, put the printed hash in a file **inside the
   container** and set `VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE` to that
   container path. A path on your computer is not visible to the app.
   One working place is the persistent `/data` directory:

   - Save the printed hash as `admin-password.hash` in this folder.
   - Start the app if it is not already running:
     `docker compose up --build --wait`
   - Copy the file into the data volume:
     `docker compose cp admin-password.hash web:/data/admin-password.hash`
   - In `.env`, set
     `VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE=/data/admin-password.hash`

   When the file variable is set, it is used instead of the inline hash.
5. Restart with `docker compose up --build --wait`.

The application never stores the password or the hash in SQLite.

Sign-in is limited to five failed attempts per client IP in a 15-minute
window. The default Compose file publishes port 8080 with
`VENUE_INVENTORY_TRUST_PROXY=false`. Docker's published-port proxy then
often presents every visitor as the same bridge address, so those five
failures pause sign-in for every administrator until the window expires,
and logs show that gateway address rather than the real client. Set
`VENUE_INVENTORY_TRUST_PROXY=true` only when a trusted HTTP reverse
proxy (Caddy, nginx) is in front and sets `X-Forwarded-For` and
`X-Forwarded-Proto`. Leave it false for the published-port setup;
otherwise a visitor could fake those headers and bypass the limiter.

## Running locally

From this repository folder:

```bash
docker compose up --build --wait
```

Open `http://localhost:8080/`.

1. Choose **Administrator sign-in**.
2. Enter `local-admin-password` (or the password you configured).
3. Choose **Manage catalog**, then add an item with a name and whole-number
   stock quantity. An optional JPEG, PNG, or WebP image is normalized to WebP
   and stored under the persistent data volume.
4. Confirm the item appears in search, and use its detail page to edit, hide,
   or delete it.
5. Choose **Sign out** and confirm the dashboard redirects back to sign-in.

Health URLs:

- Liveness: `http://localhost:8080/healthz`
- Readiness: `http://localhost:8080/readyz`

Stop it with `docker compose down`. Add `-v` only if you also want to delete
the saved database volume.

## Testing

GitHub Actions and Slipstream run the same command:

```bash
docker compose run --build --rm --no-deps verify
```

That runs formatting, linting, tests, and migration checks in the
verification image. Local `docker compose up` and VPS deploys use the
`web` service, which is the production runtime image.

On a machine with Docker, this host script runs those same checks and then
starts a throwaway Compose project to probe liveness, readiness, and data
after container replacement:

```bash
./scripts/verify.sh
```

## Repository map

| Path | Purpose |
|---|---|
| `app/` | Flask application factory, configuration, persistence, and views. |
| `app/templates/` | Jinja pages for sign-in, dashboard, and catalog management. |
| `app/static/css/` | Shared responsive CSS baseline. |
| `migrations/` | Alembic schema history. The first revision creates `web_sessions`. |
| `tests/` | HTTP-client tests for config, auth, health, and migrations. |
| `scripts/verify.sh` | Formatting, lint, tests, and (on the host) container health probes. |
| `scripts/entrypoint.sh` | Validates configuration and applies migrations before Gunicorn. |
| `scripts/deploy-vps.sh` | Pull, rebuild, restart, and verify the VPS service. |
| `compose.yaml` | Local and VPS `web` runtime service, plus a `verify` profile for checks. |
| `Dockerfile` | Non-root Flask/Gunicorn runtime image plus a verification stage. |
| `requirements.lock` | Locked runtime dependencies. |
| `requirements-dev.lock` | Locked runtime plus pytest and ruff. |
| `docs/deployment.md` | VPS deployment and recovery runbook. |
| `docs/prd/` | Approved product requirements used to derive implementation work. |
| `docs/plans/` | Implementation plans for focused, agent-ready work. |
| `docs/agents/` | Issue tracker, triage label, and domain-document conventions. |
| `AGENTS.md` / `CLAUDE.md` | Working instructions for coding agents. |

## Deployment

The current VPS workflow is unchanged: after pushing a commit, deploy with
`./scripts/deploy-vps.sh`. See [docs/deployment.md](docs/deployment.md).
HTTPS, Caddy, and production secret management remain later slices. Do not
use the local default password on a public server.
