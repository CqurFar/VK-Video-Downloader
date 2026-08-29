# Локальная проверка — то же, что в .github/workflows/ci.yml
# powershell -File ci.ps1

$ErrorActionPreference = "Stop"

Write-Host "==> Ruff lint" -ForegroundColor Cyan
uv run ruff check src
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Ruff format check" -ForegroundColor Cyan
uv run ruff format src --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Tests" -ForegroundColor Cyan
uv run pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> CLI smoke test" -ForegroundColor Cyan
uv run vk-dl --help
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> All checks passed" -ForegroundColor Green
