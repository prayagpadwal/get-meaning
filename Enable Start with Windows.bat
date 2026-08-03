@echo off
REM Make Get Meaning start automatically every time you log in.
cd /d "%~dp0"
python get_meaning.py --install-autostart
echo.
pause
