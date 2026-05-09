# Recipe Site

A private Django recipe-sharing site for friends and family. The first version is invite-only, self-hostable with Podman Compose, and stores SQLite data plus uploaded photos in local volumes.

## Features

- Invite-code signup with Django admin management.
- Authenticated recipe browsing, search, detail pages, and print-friendly pages.
- Recipe creation and owner/staff editing with ingredients, instructions, prep/cook time, servings, source URL, and photo upload.
- Authenticated comments and one rating per user per recipe.

## Local Development

This project uses `uv`. For local and agent-driven work, keep repository tooling isolated under `~/Software/recipe-site-home`:

```sh
cd ~/Software/recipe-site-home/recipe-site
source scripts/project-env.sh
uv sync
uv run python manage.py createsuperuser
./scripts/start-web.sh
```

If `uv` is not available after sourcing `scripts/project-env.sh`, install it into the isolated project home:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
source scripts/project-env.sh
```

Create invite codes in the Django admin at `http://127.0.0.1:8000/admin/`, then share those codes with friends and family. The same `scripts/start-web.sh` script starts the app locally and serves as the container entrypoint.

## Podman Compose

Copy the environment example before running:

```sh
cp .env.example .env
podman compose up --build
```

The Compose setup is intended for rootless Podman. It exposes the app on `http://localhost:8000` and stores data in named volumes:

- `recipe_data` for the SQLite database.
- `recipe_media` for uploaded recipe photos.

Before exposing the site beyond a trusted local network, set a strong `DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=false`, configure `DJANGO_ALLOWED_HOSTS`, and consider enabling `DJANGO_SECURE_SSL=true` behind HTTPS.

## Checks

```sh
source scripts/project-env.sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python manage.py check
```

## Importing Recipes

The first version stores a recipe source URL but does not scrape or import recipe content from other sites. Robust link importing should be designed after the core recipe workflow has been used for a while.

## Windows Reboot Startup

If you deploy on a Windows mini PC with WSL, use `scripts/windows-startup.ps1` to recover the app after reboot. It:

- starts the compose stack inside WSL,
- refreshes Windows `portproxy` to the current WSL IP,
- ensures a Private firewall rule for port `8000`,
- optionally re-enables Tailscale Funnel.

The default WSL project directory is `~/recipe-home/recipe-site`. Override with `-WslProjectDir` if your clone lives elsewhere.

Run from elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-startup.ps1 -DistroName Ubuntu -WslProjectDir "~/recipe-home/recipe-site" -EnableFunnel
```

To run it automatically at boot, create a Task Scheduler task that runs as highest privileges and executes:

```text
powershell.exe -ExecutionPolicy Bypass -File C:\path\to\recipe-site\scripts\windows-startup.ps1 -DistroName Ubuntu -WslProjectDir "~/recipe-home/recipe-site" -EnableFunnel
```
