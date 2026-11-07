@echo off
REM Start LessonForge (after setup.bat has been run once). Double-click me.
cd /d "%~dp0"
if not exist .venv ( echo Run setup.bat first. & pause & exit /b 1 )
call .venv\Scripts\activate.bat
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000
pause
