# Slice 8 — CI-gated HTTPS deployment and automatic rollback

**Status:** Ready for implementation  
**Blocked by:** Slice 7

## Outcome

Launch the completed proof of concept at
`https://inventory.needleminder.app` through the existing Caddy ingress, with
an operator-free deployment process that verifies CI, backs up, migrates,
health-checks, and rolls back on failure without disturbing Needleminder.

## Scope

- Keep the Venue Inventory repository, Compose project, container, data, and
  deployment script separate from Needleminder.
- Join the application to Caddy's existing external Docker network without
  publishing its application port publicly.
- Create Porkbun DNS for `inventory.needleminder.app` using authenticated
  automation available to the coding agent.
- Back up `/apps/needle-minder/Caddyfile`, add only the isolated inventory
  reverse-proxy block, validate the entire configuration, and reload Caddy.
- Store production secrets in a root-readable environment file outside Git and
  generate the initial admin password for one-time handoff.
- Extend deployment to require the target commit's GitHub CI checks, refuse
  dirty/divergent checkout state, record the prior healthy SHA, verify the
  pre-deployment backup, build before replacement, apply migrations, wait for
  readiness, and test the public HTTPS route.
- On migration, startup, or smoke failure, restore the matching backup and prior
  SHA, restart, and verify rollback.
- Verify the original `needleminder.app` before and after ingress reload.
- Remove the public UFW TCP 8080 rule only after HTTPS succeeds.
- Update README and deployment runbooks with the final agent-owned process.

## Acceptance criteria

- [ ] DNS resolves `inventory.needleminder.app` to the VPS and Caddy serves a
      trusted certificate with HTTP redirected to HTTPS.
- [ ] Venue Inventory is reachable only through Caddy; raw public port 8080 is
      closed.
- [ ] Needleminder remains available before, during, and after Caddy reload.
- [ ] Deployment refuses a target SHA without successful required CI checks.
- [ ] Deployment automatically performs and verifies backup, build, migration,
      container readiness, and public smoke tests.
- [ ] An induced health failure automatically restores the prior application
      SHA and data backup and reports the rollback outcome.
- [ ] Re-running setup/deployment is idempotent and does not duplicate DNS,
      Caddy, timer, network, or firewall configuration.
- [ ] Secrets and generated admin credentials never enter Git or normal logs.
- [ ] Operator handoff consists only of the HTTPS URL and one-time admin
      credential; no terminal, GitHub, DNS, Docker, or VPS action is required.

## Testing approach

- Unit-test deployment decisions with stubbed GitHub, Git, backup, migration,
  Compose, Caddy, DNS, firewall, and HTTP commands.
- Run an isolated Compose deployment smoke with real migrations and readiness.
- Validate Caddy configuration before any reload and compare both public
  hostnames afterward.
- Perform a controlled failure drill on a non-live port/data copy before using
  automatic rollback in production.

## Out of scope

Changing Needleminder application source, merging Compose projects, automatic
deployment on every `main` push, offsite backup, monitoring services, and
multi-host high availability are excluded.

## Suggested implementation models

- **Anthropic:** Claude Opus 5 at high effort
- **OpenAI:** GPT-5.6 Sol at high reasoning effort

## Top runtime failure points

1. Caddy cannot resolve the application on the shared network. The new hostname
   returns 502 while Needleminder works; validate network membership and proxy
   resolution before removing port 8080.
2. A migration succeeds but the new container fails readiness. The public route
   becomes unavailable or schema-incompatible; the deployment must restore the
   paired data backup and prior SHA before reporting failure.
3. DNS or certificate issuance is incomplete. The hostname does not resolve or
   shows a TLS error; verify authoritative DNS and certificate readiness before
   altering the firewall or declaring launch complete.
