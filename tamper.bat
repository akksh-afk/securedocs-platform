@echo off
cd /d "%~dp0backend"
node scripts/tamper.js %1
pause
