@echo off
rem Starts Move Radar hidden (no window, no browser). Used by the "MoveRadar" logon task.
rem Output goes to data\server.log. Open http://127.0.0.1:8765 or run.bat to see it.
cd /d "%~dp0"
start "" "C:\Users\thaka\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\pythonw.exe" -m radar.server --background
