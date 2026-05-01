# Agent Working Agreement

This repository is expected to be developed primarily by AI agents. Keep this file as a compact map of the project operating model; put detailed, active instructions in focused `.cursor/rules/*.mdc` files.

## Context Model

- `AGENTS.md` is the human-readable overview and index.
- `alwaysApply: true` `.mdc` files are the active instructions that should stay concise and non-duplicative.
- File, language, or subsystem-specific guidance should live in scoped `.mdc` files with `globs`.
- If guidance conflicts or becomes stale, update the relevant rule instead of adding another duplicate instruction.

## Current Always-On Rules

- `.cursor/rules/project-working-agreement.mdc`: core collaboration guardrails.
- `.cursor/rules/autonomous-development.mdc`: plan, implement, verify, and report workflow.
- `.cursor/rules/environment-isolation.mdc`: isolated command and package policy.
- `.cursor/rules/security-and-privacy.mdc`: secure coding and privacy expectations.
- `.cursor/rules/quality-gates.mdc`: verification expectations before work is complete.
- `.cursor/rules/git-history.mdc`: feature-boundary commit behavior.
- `.cursor/rules/documentation-maintenance.mdc`: keeping `.mdc` context current.
- `.cursor/rules/decision-records.mdc`: durable technical decision notes.

## Current Scoped Rules

- `.cursor/rules/django-recipe-app.mdc`: Django app architecture, privacy defaults, permissions, and tooling.

## Project Posture

- Do not assume a framework, runtime, package manager, database, deployment target, or hosting model until it has been explicitly chosen.
- Add structure when the project needs it, not in anticipation of possible future needs.
- Optimize code organization for agent maintainability and navigability, while preserving enough tests, documentation, and decision records for future agents to continue safely.
- Keep user-facing recipe behavior, content ownership, accessibility, security, and long-term maintainability in mind when making product or architecture decisions.

## Rule Maintenance

- When work creates durable context, update or add the narrowest relevant `.mdc` rule.
- Avoid repeating the same policy in `AGENTS.md` and multiple always-on rules.
- Keep `AGENTS.md` short enough that reading it does not meaningfully waste context.
