# Levanta el demo: crea el entorno, genera los datos sintéticos, compila el front y sirve todo en :8000.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements.txt
if (-not (Test-Path ramo.db)) { .\.venv\Scripts\python -m backend.synth.generate }
if (-not (Test-Path frontend\node_modules)) { Push-Location frontend; npm install --no-audit --no-fund; Pop-Location }
Push-Location frontend; npm run build; Pop-Location
Write-Host "Demo en http://localhost:8000" -ForegroundColor Green
.\.venv\Scripts\python -m uvicorn backend.app:app --port 8000
