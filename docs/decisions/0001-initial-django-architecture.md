# Initial Django Architecture

Date: 2026-05-01

## Context

The site is intended for private recipe sharing between friends and family. The first version needs authenticated browsing, invite-only signup, user-submitted recipes, photos, comments, ratings, and print-friendly recipe pages. The user prefers Python/Django, self-hosting, invite codes, rootless Podman Compose, SQLite, and `uv`.

## Decision

Build a conventional server-rendered Django application with two local apps:

- `accounts` owns invite codes and invite-code signup.
- `recipes` owns recipes, ingredients, instruction steps, comments, and ratings.

Use rootless Podman Compose for self-hosting with named volumes for SQLite data and uploaded media. Use `uv` for Python dependency management. Keep recipe import from external URLs out of the MVP; store `source_url` and revisit robust importing after core usage patterns are clearer.

## Consequences

The first deployment has low operational complexity and no external service dependency. SQLite and local media are appropriate for small private use, but higher traffic, multi-instance hosting, backups, and large media libraries may later require Postgres and object storage. All recipe content is private behind Django authentication, so future public sharing must be designed intentionally rather than enabled by default.
