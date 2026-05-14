@echo off
cd /d "%~dp0"
echo Loading XTTS model and generating test voice...
echo First run takes ~2 minutes.
echo.
myenv_314\Scripts\python.exe test_xtts.py
echo.
pause
