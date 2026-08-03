@echo off
REM Stop Get Meaning from starting automatically on login.
cd /d "%~dp0"
python get_meaning.py --uninstall-autostart
echo.
pause
