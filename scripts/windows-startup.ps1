param(
    [string]$DistroName = "Ubuntu",
    [string]$WslProjectDir = "~/recipe-home/recipe-site",
    [int]$ListenPort = 8000,
    [switch]$SkipTailscaleServe = $false,
    [switch]$EnableFunnel = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-WslPrimaryIp {
    param([string]$Distro)

    $raw = wsl.exe -d $Distro -- bash -lc "hostname -I"
    if (-not $raw) {
        throw "Could not read WSL IP from distro '$Distro'."
    }

    $tokens = $raw.Trim().Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
    if ($tokens.Count -eq 0) {
        throw "No IPv4 address returned by WSL distro '$Distro'."
    }

    return $tokens[0]
}

function Ensure-FirewallRule {
    param([int]$Port)

    $ruleName = "Recipe Site WSL Port $Port"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule `
            -DisplayName $ruleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -Profile Private | Out-Null
        Write-Host "Created firewall rule '$ruleName'."
    } else {
        Write-Host "Firewall rule '$ruleName' already exists."
    }
}

function Set-PortProxy {
    param(
        [int]$Port,
        [string]$TargetIp
    )

    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$Port | Out-Null
    & netsh interface portproxy add v4tov4 `
        listenaddress=0.0.0.0 `
        listenport=$Port `
        connectaddress=$TargetIp `
        connectport=$Port | Out-Null

    Write-Host "Portproxy now forwards 0.0.0.0:$Port -> $TargetIp`:$Port."
}

Write-Host "Starting recipe-site compose stack in WSL..."
wsl.exe -d $DistroName -- bash -lc "cd $WslProjectDir && ./scripts/wsl-start-stack.sh"

$wslIp = Get-WslPrimaryIp -Distro $DistroName
Set-PortProxy -Port $ListenPort -TargetIp $wslIp
Ensure-FirewallRule -Port $ListenPort

if (-not $SkipTailscaleServe) {
    $backendUrl = "http://127.0.0.1:$ListenPort"
    Write-Host "Starting Tailscale Serve (HTTPS -> $backendUrl)..."
    & tailscale serve --bg --https=443 $backendUrl
    Write-Host "Tailscale serve status:"
    & tailscale serve status
}

if ($EnableFunnel) {
    Write-Host "Ensuring Tailscale Funnel is enabled for local port $ListenPort..."
    tailscale funnel --bg $ListenPort
}

Write-Host "Current funnel status:"
tailscale funnel status

Write-Host ""
Write-Host "Startup script completed."
Write-Host "Check local access: http://localhost:$ListenPort/"
Write-Host "Check tailnet HTTPS (Serve): https://<your-tailnet-hostname>/"

