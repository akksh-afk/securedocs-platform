@echo off
REM ===================================================================
REM  Shows the current 6-digit sign-in codes, and keeps them current.
REM
REM  The codes change every 30 seconds, so a printed-once code is often
REM  already expired by the time you have typed it. This window
REM  refreshes itself, so whatever is on screen is always valid.
REM
REM  Leave it open beside the browser during the demo.
REM  Close the window when you are done.
REM ===================================================================
title SecureDocs - sign-in codes
cd /d "%~dp0backend"

if not exist "scripts\demo-code.js" (
  echo.
  echo   Could not find the project files.
  echo   This file must stay in the SIH2026 folder next to the backend folder.
  echo.
  pause
  exit /b 1
)

:loop
cls
echo.
node scripts\demo-code.js
if errorlevel 1 (
  echo.
  echo   Could not read the codes. Is PostgreSQL running, and has the
  echo   data been seeded?  Try:  npm run seed
  echo.
  pause
  exit /b 1
)
echo   This window refreshes itself. Close it when you are done.
REM ping as a delay, not timeout.exe - timeout refuses to run whenever
REM input or output is redirected, and then the loop spins flat out.
ping -n 6 127.0.0.1 >nul
goto loop
