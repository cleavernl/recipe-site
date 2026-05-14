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
    [int]$TailscaleReadyMaxAttempts = 120,
    [int]$TailscaleReadySleepSeconds = 5,
    [bool]$EnsureTailscaleAutomaticStartup = $true,
    [string]$TailscaleAuthKeyFile = ""
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

function Invoke-TailscaleAuthKeyUp {
    param(
        [string]$TsExe,
        [string]$AuthKey
    )

    if (-not $AuthKey) {
        return
    }

    Write-Host "Running tailscale up with auth key (for headless boot before any user signs in)..."
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $TsExe up --authkey $AuthKey 2>&1
        Write-Host $out
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-TailscaleUpNoAuth {
    param([string]$TsExe)

    Write-Host "Running tailscale up (no auth key) to nudge reconnect after service start..."
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $TsExe up 2>&1
        Write-Host $out
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Resolve-TailscaleAuthKey {
    param([string]$KeyFilePath)

    $fromEnv = $env:RECIPE_SITE_TAILSCALE_AUTHKEY
    if ($fromEnv) {
        return $fromEnv.Trim()
    }

    $pathsToTry = @()
    if ($KeyFilePath) {
        $pathsToTry += $KeyFilePath
    }
    $defaultPath = Join-Path $env:ProgramData "recipe-site\tailscale-authkey.txt"
    $pathsToTry += $defaultPath

    foreach ($p in $pathsToTry) {
        if (-not $p) { continue }
        if (Test-Path -LiteralPath $p) {
            $line = (Get-Content -LiteralPath $p -ErrorAction Stop | Select-Object -First 1).Trim()
            if ($line) {
                Write-Host "Using Tailscale auth key from: $p"
                return $line
            }
        }
    }

    return ""
}

function Ensure-TailscaleWindowsServiceRunning {
    param([bool]$SetAutomaticStartup)

    $candidates = @(
        Get-Service -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match 'Tailscale' -or $_.DisplayName -match 'Tailscale'
        }
    )

    if ($candidates.Count -eq 0) {
        Write-Host @"
Warning: No Windows service matching 'Tailscale' was found. Tailscale may only be starting with your user session (tray app). Install Tailscale for Windows and ensure a Tailscale service exists, or set Tailscale to start at boot via services.msc (Startup type: Automatic).
"@
        return
    }

    foreach ($svc in $candidates) {
        if ($SetAutomaticStartup) {
            if ($svc.StartType -eq [System.ServiceProcess.ServiceStartMode]::Manual) {
                Write-Host "Setting service '$($svc.Name)' startup type to Automatic (Delayed Start) for more reliable boot ordering..."
                try {
                    Set-Service -Name $svc.Name -StartupType Automatic -ErrorAction Stop
                    $svcName = $svc.Name
                    $scOut = & sc.exe config $svcName start= delayed-auto 2>&1
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "Note: could not set delayed-auto via sc.exe (exit=$LASTEXITCODE): $scOut"
                    }
                    $svc = Get-Service -Name $svc.Name
                } catch {
                    Write-Host "Warning: could not set Automatic startup for '$($svc.Name)': $_"
                }
            } elseif ($svc.StartType -eq [System.ServiceProcess.ServiceStartMode]::Automatic) {
                try {
                    $svcName = $svc.Name
                    & sc.exe config $svcName start= delayed-auto 2>&1 | Out-Null
                } catch {
                    # Ignore; service may already be delayed.
                }
            }
        }

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
        [int]$SleepSeconds,
        [bool]$AuthKeyWasUsed
    )

    $stableLoggedOut = 0
    $consecutiveStartingOnly = 0
    $maxStartingOnlyBeforeHint = 45

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $out = (& $TsExe status 2>&1 | Out-String).Trim()
        $exit = $LASTEXITCODE

        # While the GUI/daemon is still booting, status often shows NoState plus
        # "You are logged out" + control plane errors (e.g. context canceled) — treat as transient.
        $stillStarting = $out -match '(?i)Tailscale is starting|Please wait'
        if ($stillStarting) {
            $consecutiveStartingOnly++
            Write-Host "Tailscale still starting (attempt $attempt / $MaxAttempts, exit=$exit)."
            if (-not $AuthKeyWasUsed -and $consecutiveStartingOnly -ge $maxStartingOnlyBeforeHint) {
                throw @"
Tailscale has been stuck on 'Tailscale is starting' for about $($maxStartingOnlyBeforeHint * $SleepSeconds) seconds with no auth key.
OAuth / interactive login often does not finish until a Windows user session exists, so unattended reboots can hang here.

Fix (pick one):
  1) Create a reusable auth key in the Tailscale admin console, save the secret as the only line in:
     $env:ProgramData\recipe-site\tailscale-authkey.txt
     (ACL: Administrators only.) The startup script reads this path automatically, or pass -TailscaleAuthKeyFile.
  2) Or set env RECIPE_SITE_TAILSCALE_AUTHKEY for the scheduled task (less ideal).

Then reboot and check this log again.
"@
            }
            $stableLoggedOut = 0
            Start-Sleep -Seconds $SleepSeconds
            continue
        }

        $consecutiveStartingOnly = 0

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

    throw @"
Tailscale did not become ready after $($MaxAttempts * $SleepSeconds) seconds.
If this only happens before anyone signs in to Windows, configure an auth key (see README): $env:ProgramData\recipe-site\tailscale-authkey.txt
Otherwise check network, Tailscale service, and increase -TailscaleReadyMaxAttempts or the scheduled task delay.
"@
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
        Ensure-TailscaleWindowsServiceRunning -SetAutomaticStartup $EnsureTailscaleAutomaticStartup

        $authKey = Resolve-TailscaleAuthKey -KeyFilePath $TailscaleAuthKeyFile
        $authKeyUsed = $false
        if ($authKey) {
            Invoke-TailscaleAuthKeyUp -TsExe $ts -AuthKey $authKey
            $authKeyUsed = $true
        } else {
            Invoke-TailscaleUpNoAuth -TsExe $ts
        }

        Write-Host "Waiting for Tailscale daemon (avoid NoState / boot network race)..."
        Wait-TailscaleReady `
            -TsExe $ts `
            -MaxAttempts $TailscaleReadyMaxAttempts `
            -SleepSeconds $TailscaleReadySleepSeconds `
            -AuthKeyWasUsed $authKeyUsed
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
