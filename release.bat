@echo off
cd /d "%~dp0"
set /p V=New version number (example 2.1.1):
set /p N=What changed (short):
node release.js %V% "%N%"
pause
