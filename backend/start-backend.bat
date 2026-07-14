@echo off
REM Starts the SHA Claims Verification backend (FastAPI/uvicorn).
REM Runs automatically at Windows logon via a Scheduled Task named
REM "SHA Claims Backend" (see README note in this folder).

cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
