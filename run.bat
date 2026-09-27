@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run install.bat first.
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python main.py
