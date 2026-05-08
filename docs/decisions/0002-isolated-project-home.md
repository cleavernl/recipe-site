# Isolated Project Home

Date: 2026-05-01

## Context

Cursor runs in a distrobox. Distrobox is isolated enough for development commands, but it shares user-home state closely enough with the host OS that installing tools, writing caches, or changing user config in `/home/cleavernl` can affect the host and other distroboxes.

## Decision

Use `/home/cleavernl/Software/recipe-site-home` as the isolated home for this project. The repository lives at `/home/cleavernl/Software/recipe-site-home/recipe-site`. Local project commands should source `scripts/project-env.sh`, which points `HOME` and XDG cache/config/data/state directories into `recipe-site-home`.

## Consequences

Project tools, `uv` state, Python virtualenvs, package caches, and generated user-level config stay with the project instead of leaking into the host home. Future agents must verify `HOME` and XDG paths before installing tools or running setup commands.
