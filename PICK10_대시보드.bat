@echo off
chcp 65001 >nul
title PICK10 Dashboard

cd /d "%~dp0"

echo.
echo  ====================================================
echo    PICK10 Dashboard - Opening browser...
echo  ====================================================
echo.

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo  [ERROR] venv activation failed.
    pause
    exit /b 1
)

streamlit run dashboard.py

pause
