# VPS deployment

Venue Inventory runs as a standalone Docker Compose project on
`prod-vps-01`. It does not share source files, a Compose project, or a
container with the existing Needleminder application.

## Live prototype

- Public URL: `http://5.78.222.116:8080/`
- Health check: `http://5.78.222.116:8080/healthz`
- Readiness check: `http://5.78.222.116:8080/readyz`
- VPS checkout: `/opt/venue-inventory`
- Compose service: `web`
- Git branch: `main`

The prototype is intentionally served over plain HTTP on a dedicated port.
The page asks search engines not to index it, but the URL is not private or
authenticated. Add TLS and authentication before storing sensitive inventory
data.

The Compose `web` service publishes port 8080 with
`VENUE_INVENTORY_TRUST_PROXY=false`. Docker's published-port proxy typically
makes every inbound connection look like the bridge gateway, so administrator
sign-in rate limits and `client_ip` log fields are shared across visitors.
When Caddy (or another HTTP reverse proxy) is placed in front and sets
`X-Forwarded-For` and `X-Forwarded-Proto`, set
`VENUE_INVENTORY_TRUST_PROXY=true` so those values are used. Do not enable
it for the raw published port; clients could spoof the forwarded headers.

## Data layout

Durable application state lives in the Compose volume mounted at `/data`:

- Database: `/data/venue-inventory.sqlite3`
- Normalized catalog images: `/data/images/`

Operational files such as the snapshot lock and restore staging directories
are also under `/data` and are not included in backups.

Verified backups are written to a separate host directory mounted at
`/backups`. The default is `./backups` next to the Compose file, which on the
VPS is `/opt/venue-inventory/backups`.

## Deploy the latest committed change

The coding machine already has an SSH profile named `prod-vps-01`. From the
repository root, a coding agent should:

1. Confirm the intended changes are committed and pushed to `origin/main`.
2. Run:

   ```bash
   ./scripts/deploy-vps.sh
   ```

3. Confirm that the script reports `Deployment is healthy`.
4. Open `http://5.78.222.116:8080/` and confirm the changed page appears.

The script connects through SSH, clones the public repository on its first
run, and subsequently uses a fast-forward-only Git update. It builds the
replacement image, creates and verifies a pre-deployment backup while the
current application is still serving, and only then replaces the container
(which applies migrations). If backup or verification fails, the script
stops and does not migrate. It waits up to 60 seconds for the health
endpoint and refuses to overwrite uncommitted files on the VPS.

To deploy a different branch temporarily:

```bash
DEPLOY_REF=my-branch ./scripts/deploy-vps.sh
```

That branch must already be pushed to GitHub. Return to `main` by running the
normal command afterward.

## Inspect or recover the service

These commands are intended for a coding agent with SSH access:

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && docker compose ps'
ssh prod-vps-01 'cd /opt/venue-inventory && docker compose logs --tail=100 web'
ssh prod-vps-01 'curl --fail http://127.0.0.1:8080/healthz'
```

If a new deployment is unhealthy, inspect the logs first. To return to a known
good revision, revert the bad commit in Git, push the revert, and rerun the
deployment script. Do not edit the VPS checkout by hand because the deployment
script deliberately stops when it finds local changes.

Automatic rollback of both the Git revision and the matching data backup is a
later slice. Until then, a coding agent restores data with the restore
commands below.

## Backup and restore

Every backup and restore command in this section is for a coding agent with
SSH access. The operator does not run them.

### Create a backup while the application is running

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && ./scripts/run-backup-vps.sh'
```

That command uses SQLite's online backup API and a lock shared with catalog
image changes. The archive includes the database, every referenced image, a
manifest, checksums, the schema revision, and the Git SHA. Temporary upload
files, lock files, WAL sidecars, and unreferenced images are omitted. The
archive is published only after verification succeeds.

### Nightly timer

Install or refresh the timer once after this slice is deployed, and again
only when the unit files change:

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && ./scripts/install-backup-timer-vps.sh'
```

The installer compares the rendered units with what systemd already has and
replaces a file only when the content changed. It then reloads systemd and
enables `venue-inventory-backup.timer`. The timer runs at 03:15 UTC, keeps
backups for 14 days, and deletes only recognized `venue-inventory-*.tar.gz`
files in the dedicated backup directory.

### Isolated restore drill

Exercise restore against a throwaway directory before touching live data.
This command must not mount or write the live `/data` volume:

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && ./scripts/restore-drill-vps.sh /opt/venue-inventory/backups/ARCHIVE.tar.gz'
```

Replace `ARCHIVE.tar.gz` with the filename printed by the backup command.
The drill restores into a temporary directory, checks that rows and image
bytes match the archive, applies schema migrations the same way a live
restore does on startup, and issues readiness plus home, administrator
sign-in, and customer sign-in HTTP requests against that isolated copy.
That migration step is required so a pre-deployment backup taken before a
schema-changing deploy still reports ready after the drill.

### Restore live data

Live restore stops the application, verifies the archive, stages the new
database and images, keeps the previous files under a `.restore-prior-*`
directory, starts the application, and checks readiness. If startup or
readiness fails, the script rolls the prior files back.

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && ./scripts/restore-vps.sh /opt/venue-inventory/backups/ARCHIVE.tar.gz'
```

Leave the `.restore-prior-*` directory in place until the restored service
has been confirmed healthy. Removing it is a later cleanup step for a
coding agent, not a requirement of a successful restore.

### Same-VPS limit and future offsite copies

These backups live on the same VPS disk as the application. They protect
against a bad deployment, a failed migration, or accidental data changes.
They do not protect against losing the VPS or the disk itself. Offsite
replication, encrypted cloud archives, and point-in-time recovery are not
part of this slice; a later change can copy the same timestamped archives
to a second location without changing the backup format.

## One-time server configuration

The server firewall must allow TCP port 8080:

```bash
ssh prod-vps-01 'ufw allow 8080/tcp comment "Venue Inventory prototype"'
```

This is a one-time step. It should not be included in routine deployments.

Before the first deployment, set `VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET`
in the VPS `.env` file to a private random value of at least 32 characters.
Keep it stable across deployments so existing customer access codes continue
to work; do not use the development value from `.env.example` on the VPS.
