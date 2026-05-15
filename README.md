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

The default WSL project directory is `~/recipe-home/recipe-site`. Override with `-WslProjectDir` if your clone lives elsewhere (use the same value you pass to the bootstrap as `-LinuxRepoRoot`, expressed as an absolute POSIX path such as `/home/you/recipe-home/recipe-site`).

### Recommended layout: one clone under WSL

Keep **one** canonical git clone **inside WSL** (for example `~/recipe-home/recipe-site`) for both Podman Compose and these scripts. Task Scheduler should **not** rely on a second full clone on `C:\` just so `-File` has a Windows path; that second tree can drift from what actually runs in WSL.

### Task Scheduler: start from NTFS (`windows-startup-bootstrap.ps1`)

The scheduled task’s **first** `-File` target must live on **NTFS** (for example under **`%ProgramData%\recipe-site\`**). Use `scripts/windows-startup-bootstrap.ps1`: it waits until `wsl.exe` can run in your distro, resolves `windows-startup.ps1` on the `\\wsl$\…` share, then launches that script with `-WslProjectDir` set to your WSL repo root.

Do **not** point the task directly at `\\wsl$\…\windows-startup.ps1`: at cold boot Windows may try to read that path **before** WSL serves the share, and the task can fail immediately.

**Install the bootstrap once (and after rare edits):** copy `scripts/windows-startup-bootstrap.ps1` from your WSL checkout to something like **`C:\ProgramData\recipe-site\windows-startup-bootstrap.ps1`**. It probes **`wsl.exe -e true`** every **1s** until success or a budget of **`WslBootMaxAttempts` × `WslBootSleepSeconds`** seconds (defaults **90 × 2 → 180s**).

Run the main script manually from elevated PowerShell (for debugging, after WSL is already up):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-startup.ps1 -DistroName Ubuntu -WslProjectDir "~/recipe-home/recipe-site" -EnableFunnel
```

Or call the main script via the `\\wsl$\…` path to your Linux clone, with a matching absolute `-WslProjectDir` (recommended for clarity).

To run startup automatically after reboot, use **Task Scheduler**. Pick **one** pattern:

### A — Site up without logging in to Windows (headless / update reboots)

Use this when the PC should serve the site after a **cold boot or Windows Update reboot** before anyone signs in at the desktop.

- **General**
  - **Run whether user is logged on or not**
  - Select **your** Windows account (the same one where Podman and WSL work when you test manually). **Do not** run the task as **SYSTEM** or **LOCAL SERVICE** for this stack.
  - Check **Run with highest privileges**
  - Windows will store your password once for this task (normal for background user tasks).
- **Trigger:** **At startup** (or **At log on** if you prefer), with a **delay of 90–120 seconds** so WSL and networking can finish coming up.
- **Actions:** **Program:** `powershell.exe` — **Arguments** (one line; set `-LinuxRepoRoot` to the **absolute path inside WSL** to your repo root—the same tree Podman uses):

```text
-NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\recipe-site\windows-startup-bootstrap.ps1" -LinuxRepoRoot /home/YOU/recipe-home/recipe-site -DistroName Ubuntu -WslLinuxUser YOUR_WSL_UNIX_USER -EnableFunnel -WslReadyMaxAttempts 90 -WslReadySleepSeconds 3
```

Replace `YOUR_WSL_UNIX_USER` with your Linux username inside Ubuntu (the one that owns the repo). Omit `-WslLinuxUser ...` if the distro default user is already correct. Omit **`-EnableFunnel`** if you do not want Funnel. Add **`-TailscaleExe "C:\Program Files\Tailscale\tailscale.exe"`** only if the log says `tailscale.exe not found` (scheduled tasks sometimes have a minimal `PATH`).

### B — Only after you sign in (simpler debugging)

- **Trigger:** **At log on** for your account (or **At startup** with **Run only when user is logged on** if you use auto-logon and always have a session).
- **General:** **Run only when user is logged on**, **Run with highest privileges**, optional **30–60 s** delay.
- Same **Actions** line as pattern A.

### Tag-based deploy (optional)

Production can deploy on **`v*`** tags using a self-hosted GitHub Actions runner; see `.github/workflows/deploy-on-tag.yml`. Set repository variable **`RECIPE_SITE_DEPLOY_PATH`** to the **same** absolute WSL path as **`-LinuxRepoRoot`** so reboot, manual compose, and CI deploy all agree.

The workflow only runs **compose in WSL**. **Tailscale Serve** on Windows still uses **`netsh` portproxy** to reach WSL. If you see **502** on your MagicDNS URL right after a deploy but **`curl http://127.0.0.1:8000/`** works inside WSL, refresh portproxy using **After `podman compose` restarts in WSL only** below (run the startup task or **`windows-startup.ps1`**).

### After `podman compose` restarts in WSL only

If you run **`podman compose down`** / **`up`** (or **`podman-compose …`**) **only inside WSL** and do **not** re-run **`windows-startup.ps1`** on Windows, **`netsh interface portproxy`** can still send **`0.0.0.0:8000`** to an **old WSL IPv4**. Then your **tailnet HTTPS URL** (Serve) and **`http://localhost:8000`** on **Windows** fail even when **`curl http://127.0.0.1:8000/`** inside **WSL** works.

**Fix:** run the scheduled startup task (**Run**), or run **`windows-startup.ps1`** / the **bootstrap** from elevated PowerShell the same way Task Scheduler does, or reboot so that job runs. That refreshes portproxy to the current WSL address.

**Quick check:** in WSL, **`hostname -I`** (use the address Windows should forward to—often the first). On Windows (PowerShell or `cmd`), **`netsh interface portproxy show v4tov4`** — the row listening on **8000** must use that same address as **`connectaddress`**. If portproxy matches but Serve still returns **502**, confirm the app listens on the WSL **eth0** address (not only rootless **`127.0.0.1`** port-map): **`network_mode: host`** for `web` in **`compose.yaml`** is the production default in this repo.

**`curl` and HTTP 400:** Django’s **`DJANGO_ALLOWED_HOSTS`** applies to the **`Host`** header. A bare **`curl`** to the WSL eth0 URL sends that IP as **`Host`**, which is usually **not** in `ALLOWED_HOSTS`, so you see **400** even when the stack is healthy. To test the **eth0 TCP path** without changing `.env`, send an allowed host name, e.g. **`curl -fsS -H 'Host: localhost' "http://$(hostname -I | awk '{print $1}'):8000/login/"`** (expect **200** or a redirect). Compare with **`curl -fsS http://127.0.0.1:8000/login/`** (this project uses **`/login/`**, not Django’s default **`/accounts/login/`**).

If Tailscale only works **after you sign in to Windows**, these causes are common:

1. **Service startup:** The Tailscale **Windows service** may be **Manual** or only the **tray app** runs in your session. Open **`services.msc`**, find the Tailscale-related service, set **Startup type** to **Automatic** (or **Automatic (Delayed Start)**). The startup script also tries **Manual → Automatic** by default (`EnsureTailscaleAutomaticStartup`, default **true**); pass **`-EnsureTailscaleAutomaticStartup:$false`** on the **bootstrap** task arguments (it forwards to `windows-startup.ps1`) to skip that.

2. **Stored login not loaded until a user session:** Even with the service **Automatic**, Windows may not apply your Tailscale **OAuth/device login** until someone signs in (profile / credential storage). Your **`startup.log`** can show **“Tailscale is starting”** for **many minutes** (e.g. 60+ attempts × 5s) and then succeed—**or** it may never finish until login. For a PC that must work **with no one at the desktop**, use a **Tailscale auth key** (non-interactive):

   - In the [Tailscale admin keys](https://login.tailscale.com/admin/settings/keys) page, create a **reusable** auth key (note expiry and tailnet policy).
   - On the mini PC, store the key **only** in a file with tight ACLs: **`C:\ProgramData\recipe-site\tailscale-authkey.txt`** (one line, no trailing spaces), readable only by **Administrators** (and the account that runs the task). Create the **`recipe-site`** folder if needed.
   - The startup script **reads that path automatically** (you do **not** need `-TailscaleAuthKeyFile` unless you use a different location). Alternatively pass **`-TailscaleAuthKeyFile "D:\path\to\key.txt"`** or set **`RECIPE_SITE_TAILSCALE_AUTHKEY`** for the scheduled task (less ideal; avoid logging it).
   - The script **starts the Tailscale service**, then **polls every 2s** (for up to **`InitialTailscaleDelaySeconds`**, default **45**) for a **default IPv4 route** and Tailscale past early **“starting”**, then runs **`tailscale up --authkey …`** **before** WSL/Podman so the daemon can connect while the stack starts. It logs **`tailscale up exit code:`** (check for non-zero). While waiting on the later **Tailscale-ready** phase, it **re-runs** `tailscale up` every **15** status polls and may **restart the Tailscale service** around polls **36** and **72** if still stuck on “starting”. **Never commit the key** to git.

3. **Still stuck until you sign in even with an auth key:** Some Windows builds defer full connectivity or vault access until an **interactive logon**. Mitigations: increase the Task Scheduler **startup delay** (e.g. **5–10 minutes**); pass **`-InitialTailscaleDelaySeconds 120`** (longer **warmup poll** budget) and **`-TailscaleReadyMaxAttempts 200`** (or raise **`-TailscaleReadySleepSeconds`**, which multiplies the **total second budget** for **1s** Tailscale status polls) on the **bootstrap** line; enable Group Policy **Computer Configuration → Administrative Templates → System → Logon → “Always wait for the network at computer startup and logon”**; last resort **auto-logon** for a dedicated service account (security tradeoff).

Without an auth key, if **`tailscale status`** stays on **“Tailscale is starting”** for about **45 × 5 seconds (~3.75 minutes)**, the script **stops with an error** pointing at the key file.

The script writes a transcript to **`%LOCALAPPDATA%\recipe-site\startup.log`**. If that file is **empty** after a failed run but Task Scheduler shows a non-zero last result, **`windows-startup.ps1` probably never started**; read **`%ProgramData%\recipe-site\bootstrap.log`** (written by **`windows-startup-bootstrap.ps1`**) for WSL wait / `wslpath` / path errors. Re-copy the bootstrap from your WSL checkout after `git pull` so logging changes apply.

If containers do not start after reboot, open **`startup.log`** on the micro PC and read the error at the bottom.

Override with **`-LogFile "D:\logs\recipe-site.txt"`** on the bootstrap (forwarded) or on a direct `windows-startup.ps1` run if you want a different path.
