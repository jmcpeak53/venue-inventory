# Venue Inventory

A self-hosted web app for managing venue inventory.

## Status

The initial standalone prototype is live. It serves a static hello-world page
from an Nginx container and includes a health-checked VPS deployment workflow.

## Running locally

Requirements: Docker with Docker Compose.

```bash
docker compose up --build
```

Open `http://localhost:8080/`. Stop it with `docker compose down`.

## Testing

Build and start the service, then check its health endpoint:

```bash
docker compose up -d --build --wait --wait-timeout 60
curl --fail http://localhost:8080/healthz
docker compose down
```

## Deployment

The prototype runs independently at `http://5.78.222.116:8080/` on
`prod-vps-01`. It does not change the existing Caddy container or the
Needleminder project serving ports 80 and 443.

After pushing a commit to GitHub, deploy it from this repository with:

```bash
./scripts/deploy-vps.sh
```

See [docs/deployment.md](docs/deployment.md) for the exact agent workflow,
health checks, security boundary, and recovery commands.

## Repository map

| Path | Purpose |
|---|---|
| `public/` | Static web application files. |
| `scripts/deploy-vps.sh` | Pull, rebuild, restart, and verify the VPS service. |
| `docs/deployment.md` | VPS deployment and recovery runbook. |
| `docs/prd/` | Approved product requirements used to derive implementation work. |
| `docs/plans/` | Implementation plans for focused, agent-ready work. |
| `docs/agents/` | Issue tracker, triage label, and domain-document conventions. |
| `.claude/skills/` | Project-local planning skills sourced from Slipstream. |
| `AGENTS.md` / `CLAUDE.md` | Working instructions for coding agents. |
