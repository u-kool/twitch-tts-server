@echo off
cd /d "F:\Desktop\3223\333"

:: Kill existing Python process on port 5000
for /f "skip=4" %%a in ('netstat -ano ^| findstr /r ":5000 .*LISTENING"') do (
    for /f "tokens=5" %%b in ("%%a") do (
        if not "%%b"=="" taskkill /f /pid %%b >nul 2>&1
    )
)
timeout /t 1 /nobreak >nul

call "myenv_314\Scripts\activate.bat"
python server.py
pause