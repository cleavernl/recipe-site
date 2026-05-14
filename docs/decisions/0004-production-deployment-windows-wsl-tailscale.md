# Production deployment: Windows mini PC, WSL2, Podman, Tailscale

Date: 2026-05-09

## Context

The primary production host is a **Windows 11 Pro** mini PC. The Django app runs in **Linux containers** under **WSL2** (default distro name in docs: **Ubuntu**) using **Podman** and **podman-compose** (or `podman compose` fallback). **Tailscale** on Windows provides private tailnet access and optional **Serve** (HTTPS on 443 to local backend) and **Funnel** (public HTTPS without requiring visitors to install Tailscale).

This record captures environment facts that are not obvious from code alone and that future agents need when changing startup scripts, networking, or Django settings for production.

## Deployment topology

| Layer | Role |
|--------|------|
| Windows host | Tailscale client, Task Scheduler, `netsh` portproxy, Windows Firewall |
| WSL2 | Podman rootless, compose project, bind `0.0.0.0:8000` for the web container |
| Container | Gunicorn per `scripts/start-web.sh`; SQLite + media on named volumes per `compose.yaml` |

**Typical WSL project path (production):** `~/recipe-home/recipe-site` (see `scripts/windows-startup.ps1` default `-WslProjectDir`). Development on other machines may use a different path (e.g. isolated `recipe-site-home`); do not assume one path for all environments.

**Windows → WSL reachability:** External and tailnet clients often hit the **Windows** Tailscale IP or Funnel URL. Traffic to port **8000** on Windows is forwarded into WSL via **`netsh interface portproxy`** (listen `0.0.0.0:8000` → current WSL IPv4). WSL’s address can change after reboot; the startup script refreshes portproxy each run.

**Firewall:** A Private-profile inbound rule allows TCP **8000** (display name pattern `Recipe Site WSL Port 8000`).

## Tailscale

- **Serve:** Terminates TLS on the tailnet hostname (e.g. `https://<machine>.tail<number>.ts.net`) and proxies to **`http://127.0.0.1:8000`** on Windows. Configured from `scripts/windows-startup.ps1` unless `-SkipTailscaleServe`.
- **Funnel:** Optional public exposure; `tailscale funnel --bg 8000` when `-EnableFunnel`. Visitors do not need Tailscale accounts; threat model is wider than tailnet-only.
- **Do not** use `https://<tailnet-host>:8000` for browsers—port 8000 is plain HTTP on the app; HTTPS is via Serve on **443**.
- **Before desktop boot:** Tailscale should use a **Windows service** set to **Automatic** (or **Automatic (Delayed Start)**), not only the tray after sign-in. The startup script can promote **Manual → Automatic** when run elevated.
- **Before desktop logon (credentials):** Interactive logins (GitHub, Microsoft, etc.) often store state that is not fully available until a **user session** exists. Symptom: **`tailscale status`** stays on **“Tailscale is starting”** until someone signs in. Mitigation: put a **reusable auth key** in **`%ProgramData%\recipe-site\tailscale-authkey.txt`** (read automatically) or **`RECIPE_SITE_TAILSCALE_AUTHKEY`** / **`-TailscaleAuthKeyFile`**; the script runs **`tailscale up --authkey`** then waits. Without a key, it runs **`tailscale up`** once, then fails fast (~3.75 min) if still stuck on “starting” with a log message pointing at the key file. Keep keys out of source control and restrict ACLs.

## Django production settings (`.env`)

Operators must set at least:

- `DJANGO_DEBUG=false`
- Strong `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS` including localhost, `127.0.0.1`, the machine’s **Tailscale IP** (`100.x.x.x`), and the **MagicDNS hostname** (e.g. `phantom.tailXXXXXX.ts.net`)
- `DJANGO_CSRF_TRUSTED_ORIGINS` with matching `http://` and `https://` origins (including `https://<magicdns>/` when using Serve)
- `DJANGO_SECURE_SSL=true` when serving user traffic over HTTPS (Serve/Funnel). The app sets `SECURE_PROXY_SSL_HEADER` for `X-Forwarded-Proto` so Django does not redirect-loop behind TLS-terminating proxies.

Media uploads are served in production via **`login_required`** media routes in `config/urls.py` (not `DEBUG`-only `static()`).

## Reboot and automation

- **`scripts/wsl-start-stack.sh`:** Runs `podman-compose up -d` (or `podman compose`) from the project root inside WSL.
- **`scripts/windows-startup.ps1`:** Waits for WSL, runs the WSL script (optionally as a specific Linux user via `-WslLinuxUser`), refreshes portproxy, ensures firewall rule, **starts Tailscale-related Windows services** (prefers **Automatic delayed start** via `sc config … start= delayed-auto` when elevating), runs **`tailscale up --authkey`** when a key is resolved (**`RECIPE_SITE_TAILSCALE_AUTHKEY`**, **`-TailscaleAuthKeyFile`**, or default **`%ProgramData%\recipe-site\tailscale-authkey.txt`**), else **`tailscale up`** once, then **waits for `tailscale status`** (fails fast with guidance if stuck on “starting” without a key). Then Serve/Funnel. Resolves **`tailscale.exe`** from `PATH` or **`Program Files\Tailscale`**. Writes a **transcript** to **`%LOCALAPPDATA%\recipe-site\startup.log`** (override with `-LogFile`).

**Headless operation (no interactive Windows logon):** Use Task Scheduler **Run whether user is logged on or not** with the **same Windows account** that owns the WSL distro and rootless Podman (not **SYSTEM**). Use an **At startup** trigger with a **90–120 s delay** and optionally longer WSL polling (`-WslReadyMaxAttempts` / `-WslReadySleepSeconds`). **BitLocker** or other pre-boot unlock may still be required once per power-on; this only removes the need for a **Windows desktop sign-in**.

**Task Scheduler pitfalls:** Tasks that run as **SYSTEM** often fail for WSL + rootless Podman. Prefer the deployment **user** in all cases; choose **log on** vs **startup + run whether logged on or not** per README section A vs B.

## Operational checks

- After reboot: read `%LOCALAPPDATA%\recipe-site\startup.log` if containers are down.
- In WSL: `podman ps`, `cd ~/recipe-home/recipe-site && podman-compose logs` (path as deployed).
- Django: `manage.py` inside container for one-off admin (`createsuperuser`, etc.).

## Consequences

Changes to compose ports, WSL distro name, or Tailscale CLI flags must stay consistent across `windows-startup.ps1`, README, and operator runbooks. New ingress paths require updating `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` and testing HTTPS redirect behavior behind the proxy.
