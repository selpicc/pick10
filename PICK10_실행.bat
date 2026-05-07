@echo off
chcp 65001 >nul
title PICK10 Run

REM Move to bat file's own folder (handles Korean path automatically)
cd /d "%~dp0"

echo.
echo  ====================================================
echo    PICK10 v4 - Daily Seller Curation
echo  ====================================================
echo.

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo.
    echo  [ERROR] venv activation failed.
    echo  Check that 'venv' folder exists in current directory.
    echo.
    pause
    exit /b 1
)

python collect_5.py

echo.
echo  ====================================================
echo    Done! Check 'results' folder for the CSV file.
echo    Press any key to close this window.
echo  ====================================================
pause >nul
