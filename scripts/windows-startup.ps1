param(
    [string]$DistroName = "Ubuntu",
    [string]$WslProjectDir = "~/recipe-home/recipe-site",
    [string]$WslLinuxUser = "",
    [int]$ListenPort = 8000,
    [switch]$SkipTailscaleServe = $false,
    [switch]$EnableFunnel = $false,
    [string]$LogFile = "",
    [string]$TailscaleExe = "",
    [int]$WslReadyMaxAttempts = 45,
    [int]$WslReadySleepSeconds = 2,
    [int]$TailscaleReadyMaxAttempts = 90,
    [int]$TailscaleReadySleepSeconds = 5
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
Write-Host "Running as: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"

function Resolve-TailscaleExe {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath)) {
            throw "Tailscale not found at -TailscaleExe: $ExplicitPath"
        }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Tailscale\tailscale.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            return (Resolve-Path -LiteralPath $c).Path
        }
    }

    throw "tailscale.exe not found in PATH or under Program Files. Install Tailscale or pass -TailscaleExe."
}

function Ensure-TailscaleWindowsServiceRunning {
    $candidates = Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match 'Tailscale' -or $_.DisplayName -match 'Tailscale'
    }
    foreach ($svc in $candidates) {
        if ($svc.Status -ne 'Running') {
            Write-Host "Starting Windows service '$($svc.Name)' ($($svc.DisplayName))..."
            try {
                Start-Service -InputObject $svc -ErrorAction Stop
            } catch {
                Write-Host "Warning: could not start service '$($svc.Name)': $_"
            }
        }
    }
}

function Wait-TailscaleReady {
    param(
        [string]$TsExe,
        [int]$MaxAttempts,
        [int]$SleepSeconds
    )

    $stableLoggedOut = 0

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $out = (& $TsExe status 2>&1 | Out-String).Trim()
        $exit = $LASTEXITCODE

        # While the GUI/daemon is still booting, status often shows NoState plus
        # "You are logged out" + control plane errors (e.g. context canceled) — treat as transient.
        $stillStarting = $out -match '(?i)Tailscale is starting|Please wait'
        if ($stillStarting) {
            Write-Host "Tailscale still starting (attempt $attempt / $MaxAttempts, exit=$exit)."
            $stableLoggedOut = 0
            Start-Sleep -Seconds $SleepSeconds
            continue
        }

        $transientControlPlane = $out -match '(?i)context canceled|connection reset|timeout|temporary failure|i/o timeout'
        if ($out -match '(?i)NoState' -and $transientControlPlane) {
            Write-Host "Tailscale control plane not reachable yet (attempt $attempt / $MaxAttempts, exit=$exit)."
            $stableLoggedOut = 0
            Start-Sleep -Seconds $SleepSeconds
            continue
        }

        if ($exit -eq 0 -and $out.Length -gt 0 -and $out -notmatch '(?i)NoState' -and $out -notmatch '(?i)You are logged out') {
            $summary = ($out -split "`r?`n" | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim().Length -gt 0 } | Select-Object -First 1)
            Write-Host "Tailscale status OK on attempt $attempt (first line: $summary)"
            return
        }

        if ($out -match '(?i)You are logged out|NeedsLogin' -and -not $stillStarting) {
            $stableLoggedOut++
            if ($stableLoggedOut -ge 5) {
                throw "Tailscale stayed logged out after the daemon finished starting. Open the Tailscale app on this PC once and confirm you are signed in. Last output: $out"
            }
        } else {
            $stableLoggedOut = 0
        }

        Write-Host "Tailscale not ready yet (attempt $attempt / $MaxAttempts, exit=$exit): $out"
        Start-Sleep -Seconds $SleepSeconds
    }

    throw "Tailscale did not become ready after $($MaxAttempts * $SleepSeconds) seconds. Check network, Tailscale Windows service, and this transcript's log path. Increase -TailscaleReadyMaxAttempts or the scheduled task delay."
}

function Invoke-WslBash {
    param(
        [string]$WslExePath,
        [string]$Distro,
        [string]$LinuxUser,
        [string]$BashCommand
    )

    if ($LinuxUser) {
        & $WslExePath -d $Distro -u $LinuxUser -- bash -lc $BashCommand
    } else {
        & $WslExePath -d $Distro -- bash -lc $BashCommand
    }
}

function Wait-WslReady {
    param(
        [string]$WslExePath,
        [string]$Distro,
        [string]$LinuxUser,
        [int]$MaxAttempts,
        [int]$SleepSeconds
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $probe = Invoke-WslBash -WslExePath $WslExePath -Distro $Distro -LinuxUser $LinuxUser -BashCommand "echo wsl-ok" 2>&1
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
    param(
        [string]$WslExePath,
        [string]$Distro,
        [string]$LinuxUser
    )

    $raw = Invoke-WslBash -WslExePath $WslExePath -Distro $Distro -LinuxUser $LinuxUser -BashCommand "hostname -I" 2>&1
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
    $ts = Resolve-TailscaleExe -ExplicitPath $TailscaleExe

    Wait-WslReady `
        -WslExePath $WslExe `
        -Distro $DistroName `
        -LinuxUser $WslLinuxUser `
        -MaxAttempts $WslReadyMaxAttempts `
        -SleepSeconds $WslReadySleepSeconds

    Write-Host "Starting recipe-site compose stack in WSL..."
    $composeCmd = "cd $WslProjectDir && ./scripts/wsl-start-stack.sh"
    $composeResult = Invoke-WslBash -WslExePath $WslExe -Distro $DistroName -LinuxUser $WslLinuxUser -BashCommand $composeCmd 2>&1
    Write-Host $composeResult
    if ($LASTEXITCODE -ne 0) {
        throw "wsl-start-stack.sh exited with code $LASTEXITCODE"
    }

    $wslIp = Get-WslPrimaryIp -WslExePath $WslExe -Distro $DistroName -LinuxUser $WslLinuxUser
    Set-PortProxy -Port $ListenPort -TargetIp $wslIp
    Ensure-FirewallRule -Port $ListenPort

    if (-not $SkipTailscaleServe -or $EnableFunnel) {
        Ensure-TailscaleWindowsServiceRunning
        Write-Host "Waiting for Tailscale daemon (avoid NoState / boot network race)..."
        Wait-TailscaleReady `
            -TsExe $ts `
            -MaxAttempts $TailscaleReadyMaxAttempts `
            -SleepSeconds $TailscaleReadySleepSeconds
    }

    if (-not $SkipTailscaleServe) {
        $backendUrl = "http://127.0.0.1:$ListenPort"
        Write-Host "Starting Tailscale Serve (HTTPS -> $backendUrl) using $ts ..."
        & $ts serve --bg --https=443 $backendUrl
        Write-Host "Tailscale serve status:"
        & $ts serve status
    }

    if ($EnableFunnel) {
        Write-Host "Ensuring Tailscale Funnel is enabled for local port $ListenPort..."
        & $ts funnel --bg $ListenPort
    }

    Write-Host "Current funnel status:"
    & $ts funnel status

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
