@echo off
REM dbh-tool launcher.
REM
REM Finds an interpreter that has the dependencies, rather than hardcoding one, so the
REM same checkout works on any machine. Search order:
REM
REM   1. %DBH_PYTHON%                       explicit override, always wins
REM   2. .venv next to this script          project-local venv
REM   3. %USERPROFILE%\.venvs\dbh-tool      what setup.bat creates
REM   4. python on PATH                     last resort
REM
REM Run setup.bat once on a new machine to create the venv and install requirements.
REM
REM PYTHONPATH points at src\ rather than relying on an editable install, so a bare
REM checkout works. It is set instead of changing directory, so relative paths you type
REM still resolve against wherever you are, and out\ lands in your current folder.
REM
REM   dbh                                    open the review GUI
REM   dbh gui "Las-Sample\Yaloch Maya.las"   GUI on a specific cloud
REM   dbh inspect cloud.las                  header, units, CRS, warnings
REM   dbh measure cloud.las --targets data\targets_sample.json --roi 4 --outdir out
REM   dbh --help

setlocal
set "DBH_HOME=%~dp0"
set "DBH_PY="

if defined DBH_PYTHON set "DBH_PY=%DBH_PYTHON%"

if not defined DBH_PY if exist "%DBH_HOME%.venv\Scripts\python.exe" (
    set "DBH_PY=%DBH_HOME%.venv\Scripts\python.exe"
)
if not defined DBH_PY if exist "%USERPROFILE%\.venvs\dbh-tool\Scripts\python.exe" (
    set "DBH_PY=%USERPROFILE%\.venvs\dbh-tool\Scripts\python.exe"
)
if not defined DBH_PY set "DBH_PY=python"

set "PYTHONPATH=%DBH_HOME%src;%PYTHONPATH%"

REM Check the dependencies before launching, so a missing package produces a sentence
REM you can act on instead of a traceback from deep inside an import.
"%DBH_PY%" -c "import numpy, scipy, laspy, matplotlib, yaml" 2>nul
if errorlevel 1 (
    echo [dbh] "%DBH_PY%" is missing dependencies.
    echo.
    echo Run setup.bat once to create an environment, then try again.
    echo Or point dbh at your own interpreter:
    echo     set DBH_PYTHON=C:\path\to\python.exe
    exit /b 1
)

REM No arguments means the GUI. The command line is for batch work; someone who
REM double-clicks this wants the window.
if "%~1"=="" (
    "%DBH_PY%" -m dbh_tool.cli gui
    exit /b %ERRORLEVEL%
)

"%DBH_PY%" -m dbh_tool.cli %*
exit /b %ERRORLEVEL%
