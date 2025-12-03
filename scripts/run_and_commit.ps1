param([string]$SmokeCmd = ".\.venv\Scripts\python.exe test_scrape.py")
Write-Output "Running smoke: $SmokeCmd"
$ok = & $SmokeCmd
if ($LASTEXITCODE -eq 0) {
    git add -A
    git commit -m "chore: auto-commit after successful smoke test"
    Write-Output "Smoke OK - changes committed."
} else {
    Write-Output "Smoke failed (exit code $LASTEXITCODE). No commit made."
}
