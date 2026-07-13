@echo off
setlocal
cd /d "%~dp0"

REM Prefer a built single-file bundle if present (no Python needed).
if exist "dist\ColdRead.exe" (
    start "" "dist\ColdRead.exe"
    exit /b 0
)

REM Otherwise run the GUI module. Pick an interpreter: a %PYTHON% override wins,
REM then the project venv, then a system Python. The `py` launcher is preferred
REM over `python` (which may be a Store stub).
if defined PYTHON goto check
if exist ".venv\Scripts\python.exe" (set "PYTHON=.venv\Scripts\python.exe" & goto check)
where py >nul 2>&1 && (set "PYTHON=py" & goto check)
where python >nul 2>&1 && (set "PYTHON=python" & goto check)
echo Error: no Python interpreter found. Install Python 3.10+ or set PYTHON.
pause
exit /b 1

:check
REM Fail early with an actionable message if the tool isn't installed, instead
REM of dying on an ImportError deep inside the GUI (a fresh clone has no deps).
"%PYTHON%" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('vo_format') and u.find_spec('customtkinter') else 1)" >nul 2>&1
if errorlevel 1 (
    echo Error: ColdRead's dependencies aren't installed for "%PYTHON%".
    echo Install the tool first:
    echo     "%PYTHON%" -m pip install -e .
    pause
    exit /b 1
)

"%PYTHON%" -m vo_format.gui_main
