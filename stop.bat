@echo off
REM ===================================================================
REM  Stops the SecureDocs server and text reader.
REM
REM  Matches on the command line rather than the port, so it stops the
REM  right processes even if the port was changed, and never kills an
REM  unrelated Node program you happen to have running.
REM ===================================================================
echo.
echo   Stopping SecureDocs...

powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match 'server\.js|ocr-worker\.js' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('  Stopped ' + @($p).Count + ' process(es).') } else { Write-Host '  Nothing was running.' }"

REM Close the two minimised windows the launcher opened, if they remain.
taskkill /FI "WINDOWTITLE eq SecureDocs*" /T /F >nul 2>&1

echo.
REM Full path: some shells put their own "timeout" ahead of Windows' on PATH.
%SystemRoot%\System32\timeout.exe /t 2 /nobreak >nul
