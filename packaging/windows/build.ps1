# Build the Windows .exe with PyInstaller.
# Run from the project root in PowerShell: powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

python -m venv .venv-build 2>$null
. .\.venv-build\Scripts\Activate.ps1
pip install --upgrade pip | Out-Null
pip install -r requirements.txt pyinstaller | Out-Null

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
pyinstaller packaging\pyinstaller\aws-cred-manager.spec --noconfirm

Write-Host ""
Write-Host "Built: dist\aws-cred-manager.exe"
