# Windows + WSL Startup Automation

Date: 2026-05-08

## Context

The production path now includes a Windows mini PC host, WSL-hosted Podman Compose services, and Tailscale ingress. Rebooting the host can break external reachability because WSL IP addresses may change while existing Windows `portproxy` rules continue pointing to stale addresses.

## Decision

Add two operational scripts:

- `scripts/wsl-start-stack.sh` starts the container stack inside WSL with `podman-compose` (or `podman compose` fallback).
- `scripts/windows-startup.ps1` orchestrates reboot recovery from Windows by starting the WSL stack, refreshing `portproxy` for the current WSL IP, ensuring inbound firewall access on the selected port, starting Tailscale HTTPS Serve (443 to local app port) unless skipped, and optionally re-enabling Tailscale Funnel.

## Consequences

Reboot recovery becomes repeatable and schedulable through Windows Task Scheduler. Future deployment changes that alter ingress ports, WSL distro names, or compose locations must keep script parameters synchronized.

Scheduled tasks must run as the **deployment Windows user** (the account that owns WSL and rootless Podman), **not** as **SYSTEM**. For **headless** operation (no desktop logon), use **Run whether user is logged on or not** with that user’s stored credentials, **At startup** with a long delay, and see decision **0004** / README pattern A. **At log on** remains a simpler option when an interactive session is acceptable.

The Windows script logs to `%LOCALAPPDATA%\recipe-site\startup.log`, waits for WSL, resolves `tailscale.exe` when `PATH` is minimal, and optionally runs compose as `-WslLinuxUser`.
