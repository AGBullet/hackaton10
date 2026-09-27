$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
foreach ($ServiceName in @('web','worker')) {
  $PidFile = Join-Path $ProjectRoot "data\logs\$ServiceName.pid"
  if (Test-Path -LiteralPath $PidFile) {
    $ServicePid = [int](Get-Content -LiteralPath $PidFile)
    $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ServicePid"
    if ($ProcessInfo -and $ProcessInfo.CommandLine -match 'inspector[.](app|worker)' -and $ProcessInfo.ExecutablePath -eq (Join-Path $ProjectRoot '.venv\Scripts\python.exe')) {
      $OwnedProcess = [System.Diagnostics.Process]::GetProcessById($ServicePid)
      $OwnedProcess.Kill()
      $OwnedProcess.WaitForExit(5000) | Out-Null
      Write-Output "$ServiceName остановлен; данные и очередь сохранены."
    }
  }
}
