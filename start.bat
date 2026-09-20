@echo off
REM ===================================================================
REM  SecureDocs - double-click this file.
REM
REM  It installs what is missing, sets up the database, builds the
REM  screens, starts the text reader in the background, starts the
REM  server, and opens your browser. No IDE, no terminals to manage.
REM
REM  Requirements: Node.js and PostgreSQL installed and running.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo   SecureDocs - starting up
echo   ========================
echo.

where node >nul 2>&1
if errorlevel 1 (
  echo   [X] Node.js is not installed.
  echo       Install it from https://nodejs.org and run this again.
  echo.
  pause
  exit /b 1
)

if not exist "backend\.env" (
  echo   [X] backend\.env is missing.
  echo       Copy backend\.env.example to backend\.env and fill it in.
  echo.
  pause
  exit /b 1
)

echo   [1/5] Installing components (first run takes a few minutes)...
if not exist "backend\node_modules"  ( cmd /c "cd backend  && npm install --silent" )
if not exist "frontend\node_modules" ( cmd /c "cd frontend && npm install --silent" )

echo   [2/5] Preparing the database...
cmd /c "cd backend && npm run --silent db:setup"
if errorlevel 1 (
  echo.
  echo   [X] The database could not be prepared.
  echo       Check that PostgreSQL is running and that DATABASE_URL in
  echo       backend\.env is correct.
  echo.
  pause
  exit /b 1
)

echo   [3/5] Building the screens...
cmd /c "cd frontend && npm run --silent build"
if errorlevel 1 (
  echo   [X] The screens failed to build.
  pause
  exit /b 1
)

echo   [4/5] Starting the text reader in the background...
start "SecureDocs text reader" /min cmd /c "cd backend && npm run worker"

echo   [5/5] Starting the server...
start "SecureDocs server" /min cmd /c "cd backend && npm start"

echo.
echo   Waiting for the server to come up...
powershell -NoProfile -Command "$u='http://localhost:5000/api/v1/me'; for($i=0;$i -lt 40;$i++){ try{ Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2 | Out-Null; break }catch{ if($_.Exception.Response.StatusCode.value__ -eq 401){ break } ; Start-Sleep -Milliseconds 500 } }"

start "" http://localhost:5000

echo.
echo   ===================================================================
echo     SecureDocs is running:   http://localhost:5000
echo.
echo     Sign in with
echo       Service number : DL-INS-1001
echo       Password       : Test@1234
echo       6-digit code   : run  get-code.bat  to get the current one
echo.
echo     Two small windows are now running minimised: the server and the
echo     text reader. Closing them stops the system, or run stop.bat.
echo   ===================================================================
echo.
pause
