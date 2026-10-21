@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the app's Python environment...
    %PYTHON% -m venv .venv
)

if exist ".venv\Scripts\python.exe" (
    set "APP_PYTHON=.venv\Scripts\python.exe"
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 set "APP_PYTHON=python"
) else (
    echo Virtual environment setup did not complete. Using the system Python installation.
    set "APP_PYTHON=python"
)

echo Installing or updating required packages...
%APP_PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Resetting dashboard application data for this session...
%APP_PYTHON% reset_data.py
if errorlevel 1 goto :error

echo Starting the Streamlit app. Keep this window open while using it.
%APP_PYTHON% -m streamlit run app.py
goto :eof

:error
echo.
echo Startup failed. Install Python 3.10 or later and check your internet connection.
pause
exit /b 1
