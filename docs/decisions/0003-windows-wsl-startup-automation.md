# Windows + WSL Startup Automation

Date: 2026-05-08

## Context

The production path now includes a Windows mini PC host, WSL-hosted Podman Compose services, and Tailscale ingress. Rebooting the host can break external reachability because WSL IP addresses may change while existing Windows `portproxy` rules continue pointing to stale addresses.

## Decision

Add operational scripts:

- `scripts/wsl-start-stack.sh` starts the container stack inside WSL with `podman-compose` (or `podman compose` fallback).
- `scripts/windows-startup.ps1` orchestrates reboot recovery from Windows by starting the WSL stack, refreshing `portproxy` for the current WSL IP, ensuring inbound firewall access on the selected port, starting Tailscale HTTPS Serve (443 to local app port) unless skipped, and optionally re-enabling Tailscale Funnel.
- `scripts/windows-startup-bootstrap.ps1` is the **Task Scheduler entry** on NTFS: it waits for WSL, then invokes `windows-startup.ps1` from the Linux-side repo over `\\wsl$\…` (see decision **0004** and README). Scheduled tasks must not use `\\wsl$\…\windows-startup.ps1` as the task’s first `-File` target.

## Consequences

Reboot recovery becomes repeatable and schedulable through Windows Task Scheduler, with the **bootstrap** script on **`%ProgramData%\recipe-site\`** (or similar NTFS path) as the stable `-File` target. Future deployment changes that alter ingress ports, WSL distro names, or compose locations must keep script parameters synchronized across **`windows-startup-bootstrap.ps1`**, **`windows-startup.ps1`**, and operator docs.

Scheduled tasks must run as the **deployment Windows user** (the account that owns WSL and rootless Podman), **not** as **SYSTEM**. For **headless** operation (no desktop logon), use **Run whether user is logged on or not** with that user’s stored credentials, **At startup** with a long delay, and see decision **0004** / README pattern A (bootstrap + **`LinuxRepoRoot`**). **At log on** remains a simpler option when an interactive session is acceptable.

The Windows script logs to `%LOCALAPPDATA%\recipe-site\startup.log`, waits for WSL, resolves `tailscale.exe` when `PATH` is minimal, and optionally runs compose as `-WslLinuxUser`.
