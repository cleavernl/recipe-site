param(
    [string]$DistroName = "Ubuntu",
    [string]$WslProjectDir = "~/recipe-home/recipe-site",
    [int]$ListenPort = 8000,
    [switch]$SkipTailscaleServe = $false,
    [switch]$EnableFunnel = $false,
    [string]$LogFile = "",
    [int]$WslReadyMaxAttempts = 45,
    [int]$WslReadySleepSeconds = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
if (-not (Test-Path -LiteralPath $WslExe)) {
    throw "wsl.exe not found at $WslExe"
}

if (-not $LogFile) {
    $logDir = Join-Path $env:LOCALAPPDATA "recipe-site"
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $LogFile = Join-Path $logDir "startup.log"
}

Start-Transcript -LiteralPath $LogFile -Append | Out-Null
Write-Host "Log file: $LogFile"
Write-Host "Started at $(Get-Date -Format o)"

function Wait-WslReady {
    param(
        [string]$Distro,
        [int]$MaxAttempts,
        [int]$SleepSeconds
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $probe = & $WslExe -d $Distro -- bash -lc "echo wsl-ok" 2>&1
        $exitCode = $LASTEXITCODE
        $probeText = "$probe"
        if ($exitCode -eq 0 -and ($probeText -match "wsl-ok")) {
            Write-Host "WSL distro '$Distro' responded on attempt $attempt."
            return
        }
        Write-Host "WSL not ready yet (exit=$exitCode): $probeText"
        Write-Host "Waiting for WSL ($attempt / $MaxAttempts)..."
        Start-Sleep -Seconds $SleepSeconds
    }

    throw "WSL distro '$Distro' did not become ready after $($MaxAttempts * $SleepSeconds) seconds."
}

function Get-WslPrimaryIp {
    param([string]$Distro)

    $raw = & $WslExe -d $Distro -- bash -lc "hostname -I" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "hostname -I failed in distro '$Distro': $raw"
    }
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

try {
    Wait-WslReady -Distro $DistroName -MaxAttempts $WslReadyMaxAttempts -SleepSeconds $WslReadySleepSeconds

    Write-Host "Starting recipe-site compose stack in WSL..."
    $composeResult = & $WslExe -d $DistroName -- bash -lc "cd $WslProjectDir && ./scripts/wsl-start-stack.sh" 2>&1
    Write-Host $composeResult
    if ($LASTEXITCODE -ne 0) {
        throw "wsl-start-stack.sh exited with code $LASTEXITCODE"
    }

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
        & tailscale funnel --bg $ListenPort
    }

    Write-Host "Current funnel status:"
    & tailscale funnel status

    Write-Host ""
    Write-Host "Startup script completed."
    Write-Host "Check local access: http://localhost:$ListenPort/"
    Write-Host "Check tailnet HTTPS (Serve): https://<your-tailnet-hostname>/"
} finally {
    try {
        Stop-Transcript | Out-Null
    } catch {
        # Ignore if transcript was not active.
    }
}
