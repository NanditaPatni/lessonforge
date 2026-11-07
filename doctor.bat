@echo off
REM Re-test models and rewrite .env (use when a model starts failing). Double-click me.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m app.doctor
pause
