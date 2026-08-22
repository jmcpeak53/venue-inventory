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
run, and subsequently uses a fast-forward-only Git update. It rebuilds and
restarts only the `venue-inventory` container, then waits up to 60 seconds for
the health endpoint. It refuses to overwrite uncommitted files on the VPS.

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

## One-time server configuration

The server firewall must allow TCP port 8080:

```bash
ssh prod-vps-01 'ufw allow 8080/tcp comment "Venue Inventory prototype"'
```

This is a one-time step. It should not be included in routine deployments.
