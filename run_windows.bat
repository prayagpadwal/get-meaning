@echo off
REM Double-click to start Get Meaning on Windows.
cd /d "%~dp0"
python get_meaning.py %*
pause
