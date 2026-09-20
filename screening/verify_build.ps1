$ErrorActionPreference = 'Stop'
python run_tests.py
if ($LASTEXITCODE -ne 0) { throw 'Acceptance samples failed' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Unit/integration tests failed' }
Write-Host 'VERIFIED BUILD: PASS'
