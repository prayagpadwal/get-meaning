@echo off
REM Stop any running Get Meaning instance (there's no tray icon to quit from).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -like '*get_meaning*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; 'Stopped Get Meaning.' } else { 'Get Meaning was not running.' }"
echo.
pause
