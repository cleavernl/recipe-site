# Windows + WSL Startup Automation

Date: 2026-05-08

## Context

The production path now includes a Windows mini PC host, WSL-hosted Podman Compose services, and Tailscale ingress. Rebooting the host can break external reachability because WSL IP addresses may change while existing Windows `portproxy` rules continue pointing to stale addresses.

## Decision

Add two operational scripts:

- `scripts/wsl-start-stack.sh` starts the container stack inside WSL with `podman-compose` (or `podman compose` fallback).
- `scripts/windows-startup.ps1` orchestrates reboot recovery from Windows by starting the WSL stack, refreshing `portproxy` for the current WSL IP, ensuring inbound firewall access on the selected port, and optionally re-enabling Tailscale Funnel.

## Consequences

Reboot recovery becomes repeatable and schedulable through Windows Task Scheduler. Future deployment changes that alter ingress ports, WSL distro names, or compose locations must keep script parameters synchronized.
