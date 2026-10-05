<#
.SYNOPSIS
  Local CI: lint, schema drift check, unit + integration tests, optional mock end-to-end run.
.EXAMPLE
  ./scripts/ci.ps1            # lint + all tests (Blender/ffmpeg tests skip when tools are absent)
  ./scripts/ci.ps1 -Fast      # skip tests that need Blender
  ./scripts/ci.ps1 -E2E       # also run scripts/e2e_mock.py --quick
#>
param([switch]$Fast, [switch]$E2E)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONUTF8 = "1"
Remove-Item Env:WBS_CONFIRM_PAID -ErrorAction SilentlyContinue

& $py -m ruff check src tests scripts
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }

& $py scripts/export_schemas.py | Out-Null
git diff --exit-code -- schemas
if ($LASTEXITCODE -ne 0) { throw "schemas/ is stale: commit the regenerated files" }

$marker = if ($Fast) { @("-m", "not blender") } else { @() }
& $py -m pytest -q @marker
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

if ($E2E) {
    & $py scripts/e2e_mock.py --quick
    if ($LASTEXITCODE -ne 0) { throw "e2e failed" }
}
Write-Host "CI OK"
