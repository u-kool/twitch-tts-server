@echo off
cd /d "F:\Desktop\3223\333"

:: Kill previous server instance using saved PID
if not exist server.pid goto run
set /p OLD_PID=<server.pid
del server.pid
taskkill /f /pid %OLD_PID% >nul 2>&1
timeout /t 1 /nobreak >nul
:run

call "myenv_314\Scripts\activate.bat"
python server.py
pause