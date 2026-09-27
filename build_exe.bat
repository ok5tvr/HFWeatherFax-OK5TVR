@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo  HFWeatherFax OK5TVR - Windows EXE build
echo ==============================================

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

if not exist "hamlib\bin\libhamlib-4.dll" (
    echo Preparing official 64-bit Hamlib...
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\download_hamlib.ps1"
    if errorlevel 1 goto :error
)

python -m PyInstaller --noconfirm --clean "HFWeatherFax_OK5TVR.spec"
if errorlevel 1 goto :error

echo.
echo Build finished successfully:
echo   dist\HFWeatherFax_OK5TVR.exe
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
