# smoke.ps1 - PowerShell friendly smoke test
Write-Host "Running Smoke Test..."

# ensure uploads folder
$uploads = Join-Path $PSScriptRoot "uploads"
if (Test-Path $uploads) {
    Write-Host "Uploads folder OK: $uploads"
} else {
    New-Item -ItemType Directory -Path $uploads | Out-Null
    Write-Host "Uploads folder created: $uploads"
}

# run a tiny python check using -c
python -c "import config; print('BOT TOKEN:', config.BOT_TOKEN[:10] + '****'); print('PAYMENT LINK:', config.PAYMENT_LINK)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python check failed. Check that .venv is activated and config.py exists."
} else {
    Write-Host "Smoke Test Completed OK."
}
