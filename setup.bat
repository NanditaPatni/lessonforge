@echo off
REM ============================================================
REM  LessonForge one-click setup (Windows). Double-click me.
REM  1) creates .venv  2) installs packages  3) asks for keys
REM  4) tests which AI models work now  5) starts the app
REM ============================================================
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Install Python 3.10+ from python.org and tick "Add Python to PATH". & pause & exit /b 1)
if not exist .venv (
  echo [1/4] Creating virtual environment...
  python -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo [2/4] Installing packages (1-3 minutes, do not close this window)...
python -m pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
echo [3/4] Checking your API keys and finding working models...
python -m app.doctor || (pause & exit /b 1)
echo [4/4] Starting LessonForge at http://localhost:8000  (close this window to stop)
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000
pause
