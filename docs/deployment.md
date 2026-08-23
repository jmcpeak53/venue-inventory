# VPS deployment

Venue Inventory runs as a standalone Docker Compose project on
`prod-vps-01`. It does not share source files, a Compose project, or a
container with the existing Needleminder application. Public HTTPS is
terminated by the existing Caddy container.

## Live prototype

- Public URL: `https://inventory.needleminder.app/`
- Health check: `https://inventory.needleminder.app/healthz`
- Readiness check: `https://inventory.needleminder.app/readyz`
- Needleminder (must remain healthy): `https://needleminder.app/`
- VPS checkout: `/opt/venue-inventory`
- Compose files: `compose.yaml` plus `compose.prod.yaml`
- Compose service: `web` (`container_name: venue-inventory`)
- Git branch: `main`
- Production secrets: `/etc/venue-inventory/prod.env` and
  `/etc/venue-inventory/admin-password.hash` (root, mode 0600)

Caddy is the only public ingress. The application binds `127.0.0.1:8080` on
the VPS for local health probes and is attached to the existing
`needle-minder_default` Docker network so Caddy can reverse-proxy
`venue-inventory:8080`. Session cookies are marked `Secure`, and
`VENUE_INVENTORY_TRUST_PROXY=true` because Caddy sets `X-Forwarded-For` and
`X-Forwarded-Proto`.

The operator does not run terminal, DNS, GitHub, Docker, or VPS commands.
A coding agent performs every step below.

## Deploy the latest committed change

The coding machine already has an SSH profile named `prod-vps-01` and a
GitHub token (`gh auth` or `GH_TOKEN`). Porkbun API credentials must be in
the environment as `PORKBUN_API_KEY` and `PORKBUN_SECRET_API_KEY`, or in
`~/.config/venue-inventory/porkbun.env`. From the repository root:

1. Confirm the intended changes are committed and pushed to `origin/main`.
2. Confirm the `verify` GitHub Actions check on that commit succeeded.
3. Run:

   ```bash
   ./scripts/deploy-vps.sh
   ```

4. Confirm that the script reports `Deployment is healthy` and prints the
   public URL. On first launch only, it also prints the one-time
   administrator password.
5. Open `https://inventory.needleminder.app/` and confirm the changed page
   appears. Confirm `https://needleminder.app/` still loads.

The script:

1. Resolves the target SHA and refuses it unless the required `verify` check
   is completed and successful.
2. Ensures the Porkbun A record for `inventory.needleminder.app` points at
   `5.78.222.116` without creating duplicates.
3. SSHes to the VPS, refuses a dirty or non-fast-forward checkout, and
   records the prior healthy SHA.
4. Builds the replacement image, creates and verifies a pre-deployment backup
   while the current application is still serving, then replaces the
   container (which applies migrations).
5. Joins Caddy's Docker network, adds only the inventory reverse-proxy block
   after a timestamped Caddyfile backup, validates, and reloads.
6. Smokes the public HTTPS health endpoint, HTTP-to-HTTPS redirect, and
   Needleminder.
7. Rebinds the application to `127.0.0.1` and removes the public UFW TCP 8080
   rule only after those checks pass.
8. If migration, startup, Caddy, or smoke verification fails, restores the
   paired backup (when one exists) and the prior healthy SHA, then verifies
   rollback health.

Re-running the script is idempotent: DNS, Caddy, secrets, the backup timer,
the shared Docker network, and the firewall rule are created or updated only
when missing or changed.

To deploy a different branch temporarily, that branch must already be pushed
and its `verify` check must have passed:

```bash
DEPLOY_REF=my-branch ./scripts/deploy-vps.sh
```

Return to `main` by running the normal command afterward.

## Inspect or recover the service

These commands are intended for a coding agent with SSH access:

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && docker compose --env-file /etc/venue-inventory/prod.env -f compose.yaml -f compose.prod.yaml ps'
ssh prod-vps-01 'cd /opt/venue-inventory && docker compose --env-file /etc/venue-inventory/prod.env -f compose.yaml -f compose.prod.yaml logs --tail=100 web'
ssh prod-vps-01 'curl --fail https://inventory.needleminder.app/healthz'
ssh prod-vps-01 'curl --fail https://needleminder.app/'
ssh prod-vps-01 'docker exec caddy-needle-minder wget -q -O- http://venue-inventory:8080/healthz'
```

If a new deployment is unhealthy, the deploy script should already have
rolled back. Inspect the logs first. Do not edit the VPS checkout by hand
because the deployment script deliberately stops when it finds local changes.

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
only when the unit files change. Routine deploys already run the installer
when it is present:

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

### Isolated rollback drill

Exercise automatic SHA+backup rollback against a throwaway Compose project.
This command must not change Caddy, DNS, UFW, or the live data volume:

```bash
ssh prod-vps-01 'cd /opt/venue-inventory && ./scripts/rollback-drill-vps.sh'
```

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
part of this slice.

## Production secrets

The first successful deploy generates `/etc/venue-inventory/prod.env` and
`/etc/venue-inventory/admin-password.hash` if they do not already exist. The
one-time administrator password is printed to the coding agent once and then
removed from disk. Later deploys leave those files unchanged so existing
customer access codes and sessions keep working.

Do not copy local `.env` defaults onto the VPS. Do not commit production
secrets.

## One-time server configuration

DNS, Caddy, the shared Docker network, production secrets, the backup timer,
and the public port 8080 close are owned by `./scripts/deploy-vps.sh` and
are safe to re-run. Do not add a second inventory block to the Caddyfile or
a second UFW 8080 rule by hand.
