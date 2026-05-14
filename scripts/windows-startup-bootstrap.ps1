<#
.SYNOPSIS
  Waits for WSL, then runs windows-startup.ps1 from the Linux-side git clone.

.DESCRIPTION
  Scheduled tasks must not use -File \\wsl$\...\windows-startup.ps1 as the first
  hop: at boot Windows may try to read that path before the WSL VM serves \\wsl$\
  and the task fails. Keep this small script on NTFS (e.g. copy once to
  C:\ProgramData\recipe-site\) and point Task Scheduler at it. The canonical
  repo (and windows-startup.ps1) live only under the WSL filesystem.

.PARAMETER LinuxRepoRoot
  Absolute POSIX path to the repo root inside the distro (same tree as
  -WslProjectDir for windows-startup.ps1), e.g. /home/you/recipe-home/recipe-site

  Optional parameters such as -EnableFunnel, -SkipTailscaleServe, -LogFile, -TailscaleExe,
  WSL/Tailscale wait tuning, and -EnsureTailscaleAutomaticStartup are forwarded to
  windows-startup.ps1 when you pass them (omit to use that script's defaults).

.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\recipe-site\windows-startup-bootstrap.ps1 `
    -LinuxRepoRoot /home/you/recipe-home/recipe-site
#>
param(
    [string]$DistroName = "Ubuntu",
    [Parameter(Mandatory = $true)]
    [string]$LinuxRepoRoot,
    [string]$WslLinuxUser = "",
    [int]$WslBootMaxAttempts = 90,
    [int]$WslBootSleepSeconds = 2,
    [switch]$EnableFunnel,
    [switch]$SkipTailscaleServe,
    [string]$LogFile = "",
    [string]$TailscaleExe = "",
    [string]$TailscaleAuthKeyFile = "",
    [Nullable[int]]$WslReadyMaxAttempts = $null,
    [Nullable[int]]$WslReadySleepSeconds = $null,
    [Nullable[int]]$InitialTailscaleDelaySeconds = $null,
    [Nullable[int]]$TailscaleReadyMaxAttempts = $null,
    [Nullable[int]]$TailscaleReadySleepSeconds = $null,
    [Nullable[bool]]$EnsureTailscaleAutomaticStartup = $null
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
if (-not (Test-Path -LiteralPath $WslExe)) {
    throw "wsl.exe not found at $WslExe"
}

$root = ($LinuxRepoRoot.Trim() -replace '/$', '')
if (-not $root.StartsWith("/")) {
    throw "-LinuxRepoRoot must be an absolute path inside WSL (starting with /), got: $LinuxRepoRoot"
}

Write-Host "windows-startup-bootstrap: waiting for WSL distro '$DistroName'..."
$ready = $false
for ($attempt = 1; $attempt -le $WslBootMaxAttempts; $attempt++) {
    $null = & $WslExe -d $DistroName -e true 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        Write-Host "WSL responded on attempt $attempt."
        break
    }
    Write-Host "WSL not up yet ($attempt / $WslBootMaxAttempts), sleeping ${WslBootSleepSeconds}s..."
    Start-Sleep -Seconds $WslBootSleepSeconds
}

if (-not $ready) {
    throw "WSL distro '$DistroName' did not start after $($WslBootMaxAttempts * $WslBootSleepSeconds) seconds."
}

$linuxMain = "$root/scripts/windows-startup.ps1"
$wslpathOut = & $WslExe -d $DistroName wslpath -w $linuxMain 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "wslpath failed for '$linuxMain': $wslpathOut"
}

$winMain = ($wslpathOut | Out-String).Trim()
if (-not (Test-Path -LiteralPath $winMain)) {
    throw "Resolved main script path does not exist: $winMain (from $linuxMain)"
}

Write-Host "Launching main script: $winMain"

$psArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $winMain,
    "-DistroName", $DistroName,
    "-WslProjectDir", $root
)
if ($WslLinuxUser) {
    $psArgs += @("-WslLinuxUser", $WslLinuxUser)
}

if ($EnableFunnel) {
    $psArgs += "-EnableFunnel"
}
if ($SkipTailscaleServe) {
    $psArgs += "-SkipTailscaleServe"
}
if ($LogFile) {
    $psArgs += @("-LogFile", $LogFile)
}
if ($TailscaleExe) {
    $psArgs += @("-TailscaleExe", $TailscaleExe)
}
if ($TailscaleAuthKeyFile) {
    $psArgs += @("-TailscaleAuthKeyFile", $TailscaleAuthKeyFile)
}
if ($null -ne $WslReadyMaxAttempts) {
    $psArgs += @("-WslReadyMaxAttempts", $WslReadyMaxAttempts)
}
if ($null -ne $WslReadySleepSeconds) {
    $psArgs += @("-WslReadySleepSeconds", $WslReadySleepSeconds)
}
if ($null -ne $InitialTailscaleDelaySeconds) {
    $psArgs += @("-InitialTailscaleDelaySeconds", $InitialTailscaleDelaySeconds)
}
if ($null -ne $TailscaleReadyMaxAttempts) {
    $psArgs += @("-TailscaleReadyMaxAttempts", $TailscaleReadyMaxAttempts)
}
if ($null -ne $TailscaleReadySleepSeconds) {
    $psArgs += @("-TailscaleReadySleepSeconds", $TailscaleReadySleepSeconds)
}
if ($null -ne $EnsureTailscaleAutomaticStartup) {
    $psArgs += @("-EnsureTailscaleAutomaticStartup:$EnsureTailscaleAutomaticStartup")
}

& powershell.exe @psArgs
exit $LASTEXITCODE
