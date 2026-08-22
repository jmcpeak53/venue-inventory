# Slice 7 — Automatic backup and tested restore

**Status:** Ready for implementation  
**Blocked by:** Slice 6

## Outcome

Protect the complete application state—SQLite rows and normalized images—with
automatic pre-deployment and nightly backups plus a verified, coding-agent-run
restore workflow. The operator performs no terminal work.

## Scope

- Standardize the production data layout for database and images.
- Add an application-aware backup command using SQLite's online backup API and
  a lock shared with image mutations.
- Package database, images, manifest, checksums, schema revision, and deployed
  Git SHA into a timestamped backup.
- Add a pre-deployment backup hook that must verify before migration begins.
- Add an idempotent VPS systemd service/timer for nightly backup and 14-day
  retention.
- Add a restore command that stops writes, validates checksums, stages the
  complete restore, applies ownership, starts the app, and verifies readiness.
- Add an isolated restore drill that cannot overwrite live data.
- Document same-VPS risk and future offsite extension without implementing it.

## Acceptance criteria

- [ ] One command creates a consistent, checksum-verified backup while the app
      is running.
- [ ] Backups contain database, every referenced image, manifest, migration
      revision, and Git SHA without temporary/orphan files.
- [ ] Pre-deployment workflow refuses to migrate when backup or verification
      fails.
- [ ] Nightly timer installation is idempotent and retention removes only
      backups older than 14 days in the dedicated backup directory.
- [ ] Restore refuses corrupt/incomplete archives before changing destination
      data.
- [ ] An isolated restore reproduces rows and image bytes and passes readiness
      and representative admin/customer smoke requests.
- [ ] Live restore provides a recoverable prior-data staging path until final
      verification succeeds.
- [ ] Documentation assigns every command to a coding agent, not the operator.

## Testing approach

- Run backup/restore commands against an isolated temporary data directory with
  concurrent safe reads and image activity.
- Corrupt manifests, checksums, database files, images, and schema metadata and
  assert fail-closed behavior.
- Test retention with injected time and an explicit backup root.
- Exercise systemd unit rendering/idempotence without touching the live timer in
  normal automated tests.

## Out of scope

Offsite replication, point-in-time recovery, encrypted cloud archives, and a
browser backup UI are not included.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at high effort
- **OpenAI:** GPT-5.6 Terra at high reasoning effort

## Top runtime failure points

1. Database and images are captured at different logical moments. Restore has
   missing image references; a shared backup/image-mutation lock and manifest
   validation must make the snapshot coherent.
2. The backup disk is full or unwritable. The command produces a partial archive
   that looks valid; temporary output and atomic rename must prevent publication
   and block deployment.
3. Restore targets the live directory incorrectly. Healthy data could be
   overwritten before verification; validate explicit paths, stage first, and
   preserve the prior directory until smoke checks pass.

