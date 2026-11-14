<#
Installs the `rb` command on Windows: a small rb.cmd wrapper that runs rb inside the
resume-builder container, from any folder, in Command Prompt and PowerShell alike.

Easiest: double-click scripts\install-rb.cmd. Or from a terminal:
    scripts\install-rb.cmd              install, or update after moving this folder
    scripts\install-rb.cmd -Uninstall   remove it

The wrapper goes in %LOCALAPPDATA%\resume-builder\bin (set RB_BIN_DIR to choose another folder),
which is added to your user PATH. It runs
    docker compose --progress quiet --project-directory <this folder> run --rm app rb <arguments>
so your .env and compose.yaml apply wherever you are. Docker Desktop must be running.
#>
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BinDir = if ($env:RB_BIN_DIR) { $env:RB_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'resume-builder\bin' }
$Target = Join-Path $BinDir 'rb.cmd'
$Marker = 'resume-builder: runs rb inside the app container'

function Test-Ours([string]$Path) {
    (Test-Path -LiteralPath $Path) -and ((Get-Content -LiteralPath $Path -Raw) -match [regex]::Escape($Marker))
}

# The user PATH is edited in the registry as stored: unexpanded (%USERPROFILE% stays as written),
# with its value type kept, and only our own entry is added or removed. Everything else, including
# order and a trailing ';', is left exactly as it was.
function Get-UserPath {
    $key = Get-Item -LiteralPath 'HKCU:\Environment'
    if ($key.GetValueNames() -contains 'Path') {
        return @{ Value = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); Kind = $key.GetValueKind('Path') }
    }
    return @{ Value = ''; Kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
}

function Set-UserPath([string]$Value, $Kind) {
    Set-ItemProperty -LiteralPath 'HKCU:\Environment' -Name 'Path' -Value $Value -Type $Kind
    # Tell Windows the environment changed, so terminals opened from now on see the new PATH.
    if (-not ('RbInstall.Native' -as [type])) {
        Add-Type -Namespace RbInstall -Name Native -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
    }
    $result = [UIntPtr]::Zero
    [void][RbInstall.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
}

function Test-SameDir([string]$A, [string]$B) {
    return [string]::Equals($A.TrimEnd('\'), $B.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

if ($Uninstall) {
    if (Test-Ours $Target) {
        Remove-Item -LiteralPath $Target
        Write-Output "Removed $Target."
    } else {
        Write-Output "No resume-builder rb command found at $Target."
    }
    $path = Get-UserPath
    $parts = $path.Value -split ';'
    $kept = @($parts | Where-Object { -not (Test-SameDir $_ $BinDir) })
    if ($kept.Count -ne $parts.Count) {
        Set-UserPath ($kept -join ';') $path.Kind
        Write-Output "Removed $BinDir from your user PATH."
    }
    return
}

if (-not (Test-Path -LiteralPath (Join-Path $Root 'compose.yaml'))) {
    throw "No compose.yaml in $Root. Run this script from a resume-builder folder."
}
if ((Test-Path -LiteralPath $Target) -and -not (Test-Ours $Target)) {
    throw "$Target already exists and isn't from resume-builder, so it was left alone. Set RB_BIN_DIR to install somewhere else."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warning "docker isn't on your PATH yet. Install Docker Desktop before using rb."
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$wrapper = @(
    '@echo off'
    "rem $Marker. Installed by scripts\install-rb.ps1."
    "docker compose --progress quiet --project-directory `"$Root`" -f `"$Root\compose.yaml`" run --rm app rb %*"
) -join "`r`n"
# The system's ANSI code page, which is what cmd.exe expects for a .cmd file.
[IO.File]::WriteAllText($Target, $wrapper + "`r`n", [Text.Encoding]::Default)
Write-Output "Installed $Target (uses $Root)."

$path = Get-UserPath
if (-not @($path.Value -split ';' | Where-Object { Test-SameDir $_ $BinDir })) {
    $value = if ($path.Value -eq '') { $BinDir } elseif ($path.Value.EndsWith(';')) { "$($path.Value)$BinDir;" } else { "$($path.Value);$BinDir" }
    Set-UserPath $value $path.Kind
    Write-Output "Added $BinDir to your user PATH."
}
Write-Output @"

Where rb works now:
  - New terminal windows and tabs: ready.
  - A PowerShell window that was already open: paste this line once to reload its PATH:
      `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  - VS Code's terminal: restart VS Code first; its terminals keep the PATH VS Code started with.
Then try: rb --help
"@
