# Triage labels

| Canonical role | GitHub label | Meaning |
|---|---|---|
| `needs-triage` | `needs-triage` | Maintainer evaluation required |
| `needs-info` | `needs-info` | Waiting for more information |
| `ready-for-agent` | `ready-for-agent` | Fully specified and AFK-ready |
| `ready-for-human` | `ready-for-human` | Human implementation or judgment required |
| `wontfix` | `wontfix` | Will not be actioned |

When a skill names a canonical role, use the corresponding GitHub label.
An implementation issue passed to Slipstream must have `ready-for-agent` and
must not simultaneously carry another triage-state label.

