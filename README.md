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
- starts **Tailscale Serve** (`https` on port 443 proxied to `http://127.0.0.1:8000`) unless you pass `-SkipTailscaleServe`,
- optionally re-enables Tailscale Funnel.

The default WSL project directory is `~/recipe-home/recipe-site`. Override with `-WslProjectDir` if your clone lives elsewhere.

Run from elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-startup.ps1 -DistroName Ubuntu -WslProjectDir "~/recipe-home/recipe-site" -EnableFunnel
```

To run it automatically after reboot, use **Task Scheduler**. Pick **one** pattern:

### A — Site up without logging in to Windows (headless / update reboots)

Use this when the PC should serve the site after a **cold boot or Windows Update reboot** before anyone signs in at the desktop.

- **General**
  - **Run whether user is logged on or not**
  - Select **your** Windows account (the same one where Podman and WSL work when you test manually). **Do not** run the task as **SYSTEM** or **LOCAL SERVICE** for this stack.
  - Check **Run with highest privileges**
  - Windows will store your password once for this task (normal for background user tasks).
- **Trigger:** **At startup** (or **At log on** if you prefer), with a **delay of 90–120 seconds** so WSL and networking can finish coming up.
- **Actions:** **Program:** `powershell.exe` — **Arguments** (one line; adjust paths and options):

```text
-ExecutionPolicy Bypass -File C:\path\to\recipe-site\scripts\windows-startup.ps1 -DistroName Ubuntu -WslLinuxUser YOUR_WSL_UNIX_USER -EnableFunnel -WslReadyMaxAttempts 90 -WslReadySleepSeconds 3
```

Replace `YOUR_WSL_UNIX_USER` with your Linux username inside Ubuntu (the one that owns `~/recipe-home/recipe-site`). Omit `-WslLinuxUser ...` if the distro default user is already correct. Add `-TailscaleExe "C:\Program Files\Tailscale\tailscale.exe"` only if the log says `tailscale.exe not found` (scheduled tasks sometimes have a minimal `PATH`).

### B — Only after you sign in (simpler debugging)

- **Trigger:** **At log on** for your account.
- **General:** **Run only when user is logged on**, **Run with highest privileges**, optional **30–60 s** delay.
- Same **Actions** line as pattern A (you can drop the longer WSL wait if startup is reliable).

Use the real Windows path to `scripts\windows-startup.ps1`. If the repo path differs inside WSL, set `-WslProjectDir "~/recipe-home/recipe-site"`.

If Tailscale only works **after you sign in to Windows**, the Tailscale **Windows service** is probably **Manual** or only the **tray app** starts your session. For a headless mini PC, open **`services.msc`**, find the Tailscale-related service (name often contains **Tailscale**), set **Startup type** to **Automatic** (or **Automatic (Delayed Start)**), apply, and reboot once. The startup script also tries to set **Manual → Automatic** by default (`EnsureTailscaleAutomaticStartup`, default **true**); pass **`-EnsureTailscaleAutomaticStartup:$false`** if you do not want that behavior.

If **`startup.log`** shows **`unexpected state: NoState`** or **`Tailscale is starting`** for a long time, the task may have run before the daemon or network was ready. During boot, **`You are logged out`** next to **`context canceled`** is often **transient**. The script **starts the Tailscale service**, then **polls** `tailscale status` (default up to **90 × 5 seconds**). Increase the Task Scheduler **startup delay** or **`-TailscaleReadyMaxAttempts`** / **`-TailscaleReadySleepSeconds`** if needed.

The script writes a transcript to **`%LOCALAPPDATA%\recipe-site\startup.log`**. If containers do not start after reboot, open that file on the micro PC and read the error at the bottom.

Override with `-LogFile "D:\logs\recipe-site.txt"` if you want a different path.
