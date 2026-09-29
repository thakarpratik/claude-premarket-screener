@echo off
rem Builds the pre-market dashboard, opens it in the browser, and rebuilds every
rem 5 minutes until 09:35 ET (the page auto-refreshes itself).
cd /d "%~dp0"
if not exist "%~dp0reports" mkdir "%~dp0reports"

rem After waking from sleep Wi-Fi can take a minute; wait up to ~3 minutes for the internet.
set /a tries=0
:waitnet
curl -s -o nul -m 5 https://query1.finance.yahoo.com >nul 2>&1 && goto run
set /a tries+=1
if %tries% geq 18 goto run
timeout /t 10 /nobreak >nul
goto waitnet

:run
echo ===== %date% %time% >> "%~dp0reports\run.log"
"C:\Users\thaka\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" premarket_screen.py --watch 5 >> "%~dp0reports\run.log" 2>&1
