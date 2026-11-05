# Build the Windows installer on a Windows 10/11 x64 machine:
#   desktop\release\AI-Circuit-Studio-Setup-<version>.exe
#
# Needs: git, uv (https://docs.astral.sh/uv/), Node.js 22+. KiCad is not bundled: users install KiCad 10 themselves.
# The installer is not code-signed, so Windows SmartScreen asks once before the first run.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync --group packaging
uv run --group packaging python packaging/build_backend.py
if (-not (Test-Path "build\backend\aics-backend\aics-backend.exe")) { throw "backend build failed" }

Set-Location desktop
npm ci
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"  # no signing certificate
npm run dist:win
Get-ChildItem release\*.exe | ForEach-Object { Write-Host "built $($_.FullName)" }
