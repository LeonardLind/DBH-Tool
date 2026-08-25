@echo off
REM One-time setup for a new machine.
REM
REM Creates a virtual environment and installs the package. dbh.bat finds it
REM automatically afterwards.
REM
REM The venv goes in %USERPROFILE%\.venvs\dbh-tool rather than inside this folder, on
REM purpose: this project is often kept in OneDrive or another synced directory, and a
REM venv there means syncing tens of thousands of files for no benefit. A .venv next to
REM this script is still preferred if you already have one -- dbh.bat checks it first.

setlocal
set "DBH_HOME=%~dp0"
set "VENV=%USERPROFILE%\.venvs\dbh-tool"

REM Prefer the py launcher, which knows about every installed version.
set "BOOT_PY="
py -3 --version >nul 2>&1 && set "BOOT_PY=py -3"
if not defined BOOT_PY (
    python --version >nul 2>&1 && set "BOOT_PY=python"
)
if not defined BOOT_PY (
    echo [setup] No Python found. Install Python 3.11 or newer from python.org
    echo         and make sure "Add python.exe to PATH" is ticked.
    exit /b 1
)

echo [setup] bootstrap interpreter: %BOOT_PY%
%BOOT_PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo [setup] Python 3.10 or newer is required.
    %BOOT_PY% --version
    exit /b 1
)

if exist "%VENV%\Scripts\python.exe" (
    echo [setup] reusing existing environment at %VENV%
) else (
    echo [setup] creating environment at %VENV%
    %BOOT_PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo [setup] failed to create the virtual environment.
        exit /b 1
    )
)

echo [setup] installing dbh-tool and dependencies ...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -e "%DBH_HOME%.[dev]"
if errorlevel 1 (
    echo [setup] install failed.
    exit /b 1
)

echo.
echo [setup] verifying ...
"%VENV%\Scripts\python.exe" -c "import numpy, scipy, laspy, matplotlib, yaml; print('  numpy', numpy.__version__); print('  scipy', scipy.__version__); print('  laspy', laspy.__version__); print('  matplotlib', matplotlib.__version__)"
if errorlevel 1 (
    echo [setup] verification failed -- something did not install correctly.
    exit /b 1
)
"%VENV%\Scripts\python.exe" -c "import tkinter" 2>nul
if errorlevel 1 (
    echo   WARNING: tkinter is unavailable, so the review GUI will not start.
    echo            Every measurement command still works. On Windows, reinstall
    echo            Python with the "tcl/tk and IDLE" option enabled.
)

echo.
echo [setup] running the test suite -- expect 153 passed, about 60-85 seconds ...
"%VENV%\Scripts\python.exe" -m pytest -q "%DBH_HOME%tests"
if errorlevel 1 (
    echo.
    echo [setup] TESTS FAILED. Do not trust a measurement from this checkout until
    echo         you know why -- several of those tests exist to catch silently
    echo         wrong geometry.
    exit /b 1
)

echo.
echo [setup] done. Try:
echo     dbh                                     the review GUI
echo     dbh inspect "Las-Sample\Yaloch Maya.las"
echo     dbh measure "Las-Sample\Yaloch Maya.las" --targets data\targets_sample.json --roi 4 --outdir out
exit /b 0
