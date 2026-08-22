# Venue Inventory

A self-hosted web app for managing venue inventory.

## Status

This repository is an initial project scaffold. The application stack, setup,
and test commands will be added with the first implementation plan.

## Deployment

The production app is intended to run on the existing `needleminder.app`
domain. The parent network map records `prod-vps-01` (Tailscale
`100.77.40.40`) as the public-facing VPS, currently using
`caddy-needle-minder` for `needleminder.app`. That entry is marked `[M]` in the
network map, so verify the live configuration before deploying or changing it.

## Repository map

| Path | Purpose |
|---|---|
| `docs/plans/` | Implementation plans for focused, agent-ready work. |
| `AGENTS.md` / `CLAUDE.md` | Working instructions for coding agents. |
