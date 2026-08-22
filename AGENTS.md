# Venue Inventory — Agent Instructions

> This file and `CLAUDE.md` are kept **identical**. Any change to one must be
> mirrored to the other in the same commit.

## Operator Background

Operator is an experienced systems analyst but does not know coding, or terminal usage. Step by step instructions are critical for any deployment scenarios or manual steps which the operator must take.

## Planning and Model Selection

Any time that you create a plan, suggest a model for implementation for both Anthropic and OpenAI. The focus is on high accuracy while balancing efficiency. Be conservative but not every plan needs the highest tier model for implementation.

Before finalizing any plan, identify the 3 most likely runtime failure points in the proposed implementation — describe what breaks and what the symptom would look like. This must appear in the plan output before the operator approves it.

## Approval Ergonomics

When a command is likely to be reused, request elevated execution up front with a narrow persisted `prefix_rule` instead of first running it sandboxed and retrying.

## README

Review the README.md as the first step of any prompt to ground yourself in the project.

Update `README.md` when setup steps, project structure, commands, or the v1 feature set changes. Keep it scoped to: what the app is, setup, running, testing, and where to find deeper docs.
