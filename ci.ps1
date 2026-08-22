# Локальная проверка запуска приложения
# powershell -File ci.ps1

$ErrorActionPreference = "Stop"

Write-Host "==> CLI smoke test" -ForegroundColor Cyan
uv run vk-dl --help

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "==> All checks passed" -ForegroundColor Green
