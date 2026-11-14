@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo Python was not found on this PC.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this file again.
    echo.
    pause
    exit /b 1
)

%PY% -c "import pypdf" >nul 2>nul
if errorlevel 1 (
    echo First run: installing pypdf...
    %PY% -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install pypdf. Check your internet connection, then try again.
        pause
        exit /b 1
    )
)

%PY% app.py
if errorlevel 1 pause
