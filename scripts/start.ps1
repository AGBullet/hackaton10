$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Сначала создайте .venv и установите requirements.txt' }
& $PythonExe -X utf8 -c "from inspector.db import init; from inspector.catalog import load_catalog; init(); load_catalog()"
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подключиться к PostgreSQL. Проверьте .env' }
$LogDirectory = Join-Path $ProjectRoot 'data\logs'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
try { $Health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 } catch { $Health = $null }
if (-not $Health) {
  $WebProcess = Start-Process -FilePath $PythonExe -ArgumentList @('-X','utf8','-m','uvicorn','inspector.app:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDirectory 'web.out.log') -RedirectStandardError (Join-Path $LogDirectory 'web.err.log') -PassThru
  Set-Content -LiteralPath (Join-Path $LogDirectory 'web.pid') -Value $WebProcess.Id
}
if (-not $Health -or -not $Health.worker_running) {
  $WorkerProcess = Start-Process -FilePath $PythonExe -ArgumentList @('-X','utf8','-m','inspector.worker') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDirectory 'worker.out.log') -RedirectStandardError (Join-Path $LogDirectory 'worker.err.log') -PassThru
  Set-Content -LiteralPath (Join-Path $LogDirectory 'worker.pid') -Value $WorkerProcess.Id
}
Write-Output 'Инспектор ИИ: http://127.0.0.1:8000'
Write-Output 'API: http://127.0.0.1:8000/docs'
