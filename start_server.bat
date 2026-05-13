@echo off
cd /d "F:\Desktop\3223\333"

:: Kill existing server.py process
wmic process where "name='python.exe' and CommandLine like '%%server.py%%'" delete >nul 2>&1
timeout /t 1 /nobreak >nul

call "myenv_314\Scripts\activate.bat"
python server.py
pause