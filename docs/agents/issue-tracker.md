# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues at
`jmcpeak53/venue-inventory`. Use the `gh` CLI from the repository root so it
infers the remote automatically.

## Conventions

- Create issues with `gh issue create --body-file <path>`.
- Read the complete body, comments, and labels before acting on an issue.
- Publish every multiline Markdown body through a real file. Never pass
  Markdown as an inline shell argument or encode line breaks as literal `\n`.
- Verify a published body by fetching it and comparing it byte-for-byte with
  the body file. Repair mismatches in place with `gh issue edit --body-file`.
- Apply and remove triage labels using the mapping in
  `docs/agents/triage-labels.md`.
- Do not close or modify a parent PRD while creating implementation issues.

## Slipstream planned-issue contract

Every implementation issue intended for Slipstream must contain:

- Exactly one standalone `Plan: docs/plans/<document>.md` line
- `Blocked by: None` or one standalone `Blocked by: #<number>` line for each
  prerequisite
- The `ready-for-agent` label and none of `needs-triage`, `needs-info`, or
  `wontfix`

The referenced plan must be committed on the base branch before bootstrap.

