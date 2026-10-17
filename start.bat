@echo off
rem Starts the monitor in the background (a second copy exits on its own) and opens the dashboard.
start "" pythonw "%~dp0monitor.py"
timeout /t 2 /nobreak >nul
start "" http://localhost:8765
