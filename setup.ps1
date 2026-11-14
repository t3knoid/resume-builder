<#
Sets up resume-builder on Windows: builds and starts the app, then installs the `rb` command.
Safe to run again, for example after pulling an update.

Easiest: double-click setup.bat, or type .\setup.bat in PowerShell. PowerShell needs the .\ prefix:
a bare `setup` runs an unrelated command. If your execution policy allows local scripts, .\setup.ps1
also works, and then `rb` works in that same PowerShell window right away.

First time: it creates .env from .env.example and stops, so you can fill in your details.
Docker Desktop must be running.
#>
$ErrorActionPreference = 'Stop'

function Stop-Setup([string]$Message) {
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Setup "docker isn't installed or isn't on your PATH. Install Docker Desktop, then run this again."
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Output "Created .env."
    Write-Output "Open .env, set RB_DEFAULT_USER and your other details, then run setup again."
    exit 1
}
if (Select-String -LiteralPath '.env' -Pattern '^RB_DEFAULT_USER=your-name\s*$' -Quiet) {
    Stop-Setup "RB_DEFAULT_USER in .env is still 'your-name'. Set it to your folder name, then run setup again."
}

docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "docker compose up failed (see above). Is Docker Desktop running?"
}

& (Join-Path $PSScriptRoot 'scripts\install-rb.ps1')

# Reload PATH for this session, so `rb` works here at once when this script runs in your PowerShell window.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

$port = (Select-String -LiteralPath '.env' -Pattern '^RB_HOST_PORT=(\d+)' | Select-Object -First 1)
$port = if ($port) { $port.Matches[0].Groups[1].Value } else { '8000' }
Write-Output ""
Write-Output "resume-builder is running at http://localhost:$port"
