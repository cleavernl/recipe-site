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
    [int]$TailscaleReadyMaxAttempts = 150,
    [int]$TailscaleReadySleepSeconds = 5,
    [bool]$EnsureTailscaleAutomaticStartup = $true,
    [string]$TailscaleAuthKeyFile = "",
    [int]$InitialTailscaleDelaySeconds = 45
)

# WslReadySleepSeconds / TailscaleReadySleepSeconds: each pair multiplies to a total wait
# budget in seconds; WSL and Tailscale readiness loops poll every 1s until success or budget.

# Task Scheduler: do not use -File \\wsl$\<distro>\...\windows-startup.ps1 as the task's
# primary script. At boot, \\wsl$\ may be unreadable until WSL is running. Keep a small
# copy of scripts/windows-startup-bootstrap.ps1 on NTFS (e.g. under %ProgramData%) and
# point the scheduled task at that file; it waits for WSL then runs this script from the
# Linux-side clone (single repo tree under WSL for app + these scripts).

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

function Test-WindowsDefaultRoutePresent {
    try {
        $r = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Select-Object -First 1
        return $null -ne $r
    } catch {
        return $false
    }
}

function Test-IPv4InternetIcmp {
    param([string[]]$Hosts = @("1.1.1.1", "8.8.8.8"))
    foreach ($h in $Hosts) {
        if (Test-Connection -ComputerName $h -Count 1 -Quiet -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    return $false
}

function Wait-InitialTailscaleNetworkWarmup {
    param(
        [string]$TsExe,
        [int]$MaxWaitSeconds
    )

    if ($MaxWaitSeconds -le 0) {
        return
    }

    Write-Host "Polling up to ${MaxWaitSeconds}s (2s interval) for default route + Tailscale past early 'starting' before first tailscale up..."
    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $route = Test-WindowsDefaultRoutePresent
        $ping = Test-IPv4InternetIcmp
        $st = (& $TsExe status 2>&1 | Out-String).Trim()
        $tsPastEarly = $st -notmatch "(?i)Tailscale is starting|Please wait"
        if ($route -and $tsPastEarly) {
            if ($ping) {
                Write-Host "Warmup checks passed (route + Tailscale ready, ICMP OK); proceeding to tailscale up..."
            } else {
                Write-Host "Warmup checks passed (route + Tailscale ready); proceeding to tailscale up..."
            }
            return
        }
        if (-not $route) {
            Write-Host "Warmup: waiting for default IPv4 route..."
        } elseif (-not $tsPastEarly) {
            $snip = if ($st.Length -le 160) { $st } else { $st.Substring(0, 160) + "..." }
            Write-Host "Warmup: waiting for Tailscale past early start (status: $snip)"
        } else {
            Write-Host "Warmup: unexpected state; retrying..."
        }
        Start-Sleep -Seconds 2
    }

    Write-Host "Warmup deadline (${MaxWaitSeconds}s) reached without all checks passing; proceeding to tailscale up anyway."
}

function Invoke-TailscaleAuthKeyUp {
    param(
        [string]$TsExe,
        [string]$AuthKey
    )

    if (-not $AuthKey) {
        return 0
    }

    Write-Host "Running tailscale up with auth key (for headless boot before any user signs in)..."
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $TsExe up --authkey=$AuthKey 2>&1 | Out-String
        $code = $LASTEXITCODE
        Write-Host "tailscale up exit code: $code"
        if ($out.Trim().Length -gt 0) {
            Write-Host $out.TrimEnd()
        } else {
            Write-Host "(tailscale up produced no stdout/stderr)"
        }
        return $code
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Restart-TailscaleWindowsServiceOnce {
    $svc = Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match 'Tailscale' -or $_.DisplayName -match 'Tailscale'
    } | Select-Object -First 1

    if (-not $svc) {
        return
    }

    Write-Host "Restarting Windows service '$($svc.Name)' to recover stuck 'Tailscale is starting'..."
    try {
        Restart-Service -InputObject $svc -Force -ErrorAction Stop
        $svcName = $svc.Name
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            $s = Get-Service -Name $svcName -ErrorAction SilentlyContinue
            if ($s -and $s.Status -eq "Running") {
                Write-Host "Tailscale service '$svcName' is Running after restart."
                return
            }
            Start-Sleep -Seconds 1
        }
        Write-Host "Warning: Tailscale service '$svcName' not Running after restart wait window."
    } catch {
        Write-Host "Warning: Tailscale service restart failed: $_"
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
                $svcName = $svc.Name
                $startDeadline = (Get-Date).AddSeconds(45)
                while ((Get-Date) -lt $startDeadline) {
                    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
                    if ($svc -and $svc.Status -eq "Running") {
                        Write-Host "Service '$svcName' is Running."
                        break
                    }
                    Start-Sleep -Seconds 1
                }
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
        [bool]$AuthKeyWasUsed,
        [string]$AuthKeyForRetry = ""
    )

    $pollSeconds = 1
    $maxWaitSeconds = $MaxAttempts * [Math]::Max(1, $SleepSeconds)
    $deadline = (Get-Date).AddSeconds($maxWaitSeconds)
    $stableLoggedOut = 0
    $consecutiveStartingOnly = 0
    # Match old wall-clock: previously 45 iterations × SleepSeconds between polls in "starting" only.
    $maxStartingPollsWithoutAuth = 45 * [Math]::Max(1, $SleepSeconds)
    $serviceRestartCount = 0
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++
        $out = (& $TsExe status 2>&1 | Out-String).Trim()
        $exit = $LASTEXITCODE

        # While the GUI/daemon is still booting, status often shows NoState plus
        # "You are logged out" + control plane errors (e.g. context canceled) — treat as transient.
        $stillStarting = $out -match '(?i)Tailscale is starting|Please wait'
        if ($stillStarting) {
            $consecutiveStartingOnly++
            Write-Host "Tailscale still starting (poll $attempt, exit=$exit, budget ${maxWaitSeconds}s)."

            if ($AuthKeyForRetry -and ($attempt % 15 -eq 0)) {
                Write-Host "Re-trying tailscale up --authkey (periodic nudge while starting)..."
                [void](Invoke-TailscaleAuthKeyUp -TsExe $TsExe -AuthKey $AuthKeyForRetry)
            }

            if ($AuthKeyForRetry -and $attempt -eq 36 -and $serviceRestartCount -lt 1) {
                Restart-TailscaleWindowsServiceOnce
                $serviceRestartCount++
                [void](Invoke-TailscaleAuthKeyUp -TsExe $TsExe -AuthKey $AuthKeyForRetry)
            }
            if ($AuthKeyForRetry -and $attempt -eq 72 -and $serviceRestartCount -lt 2) {
                Restart-TailscaleWindowsServiceOnce
                $serviceRestartCount++
                [void](Invoke-TailscaleAuthKeyUp -TsExe $TsExe -AuthKey $AuthKeyForRetry)
            }

            if (-not $AuthKeyWasUsed -and $consecutiveStartingOnly -ge $maxStartingPollsWithoutAuth) {
                throw @"
Tailscale has been stuck on 'Tailscale is starting' for about $maxStartingPollsWithoutAuth seconds with no auth key.
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
            Start-Sleep -Seconds $pollSeconds
            continue
        }

        $consecutiveStartingOnly = 0

        $transientControlPlane = $out -match '(?i)context canceled|connection reset|timeout|temporary failure|i/o timeout'
        if ($out -match '(?i)NoState' -and $transientControlPlane) {
            Write-Host "Tailscale control plane not reachable yet (poll $attempt, exit=$exit)."
            $stableLoggedOut = 0
            Start-Sleep -Seconds $pollSeconds
            continue
        }

        if ($exit -eq 0 -and $out.Length -gt 0 -and $out -notmatch '(?i)NoState' -and $out -notmatch '(?i)You are logged out') {
            $summary = ($out -split "`r?`n" | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim().Length -gt 0 } | Select-Object -First 1)
            Write-Host "Tailscale status OK on poll $attempt (first line: $summary)"
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

        Write-Host "Tailscale not ready yet (poll $attempt, exit=$exit): $out"
        Start-Sleep -Seconds $pollSeconds
    }

    throw @"
Tailscale did not become ready within $maxWaitSeconds seconds.
If status stayed on 'Tailscale is starting' until you signed in, Windows may be deferring full network or user-vault access until an interactive session. Try: (1) increase Task Scheduler startup delay and -InitialTailscaleDelaySeconds; (2) enable Group Policy 'Always wait for the network at computer startup and logon'; (3) increase -TailscaleReadyMaxAttempts or -TailscaleReadySleepSeconds (they set the total wait budget); (4) as last resort use auto-logon for a dedicated service account (security tradeoff).
Auth key file: $env:ProgramData\recipe-site\tailscale-authkey.txt
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

    $pollSeconds = 1
    $maxWaitSeconds = $MaxAttempts * [Math]::Max(1, $SleepSeconds)
    $deadline = (Get-Date).AddSeconds($maxWaitSeconds)
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++
        $probe = Invoke-WslBash -WslExePath $WslExePath -Distro $Distro -LinuxUser $LinuxUser -BashCommand "echo wsl-ok" 2>&1
        $exitCode = $LASTEXITCODE
        $probeText = "$probe"
        if ($exitCode -eq 0 -and ($probeText -match "wsl-ok")) {
            Write-Host "WSL distro '$Distro' responded on poll $attempt."
            return
        }
        Write-Host "WSL not ready yet (exit=$exitCode): $probeText"
        Write-Host "Waiting for WSL (poll $attempt, budget ${maxWaitSeconds}s)..."
        Start-Sleep -Seconds $pollSeconds
    }

    throw "WSL distro '$Distro' did not become ready within $maxWaitSeconds seconds."
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

    $authKey = ""
    $authKeyUsed = $false

    if (-not $SkipTailscaleServe -or $EnableFunnel) {
        Ensure-TailscaleWindowsServiceRunning -SetAutomaticStartup $EnsureTailscaleAutomaticStartup

        $authKey = Resolve-TailscaleAuthKey -KeyFilePath $TailscaleAuthKeyFile
        Wait-InitialTailscaleNetworkWarmup -TsExe $ts -MaxWaitSeconds $InitialTailscaleDelaySeconds
        if ($authKey) {
            [void](Invoke-TailscaleAuthKeyUp -TsExe $ts -AuthKey $authKey)
            $authKeyUsed = $true
        } else {
            Invoke-TailscaleUpNoAuth -TsExe $ts
        }
    }

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
        Write-Host "Waiting for Tailscale daemon (avoid NoState / boot network race)..."
        Wait-TailscaleReady `
            -TsExe $ts `
            -MaxAttempts $TailscaleReadyMaxAttempts `
            -SleepSeconds $TailscaleReadySleepSeconds `
            -AuthKeyWasUsed $authKeyUsed `
            -AuthKeyForRetry $authKey
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
