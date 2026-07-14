@echo off
REM Starts the SHA Claims Verification backend (FastAPI/uvicorn) and keeps
REM it running: if uvicorn ever exits (crash, unhandled error, etc.) this
REM relaunches it automatically after a short pause. Runs automatically at
REM Windows logon via the shortcut in the Startup folder.

cd /d "%~dp0"

:loop
"%~dp0venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo [start-backend] uvicorn exited — restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
