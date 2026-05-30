@echo off
chcp 65001 >nul
title PICK10 Browser Install

REM Move to bat file's own folder (handles Korean path automatically)
cd /d "%~dp0"

echo.
echo  ====================================================
echo    PICK10 - Headless Browser Install (one-time)
echo    SPA 사이트(코코핏 등) 정확 수집용
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

echo  [1/2] Installing playwright package...
python -m pip install playwright
if errorlevel 1 (
    echo.
    echo  [ERROR] pip install playwright failed.
    pause
    exit /b 1
)

echo.
echo  [2/2] Downloading Chromium engine (큰 용량, 시간 걸려요)...
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo  [ERROR] chromium download failed.
    pause
    exit /b 1
)

echo.
echo  ====================================================
echo    설치 완료! 이제 코코핏 같은 SPA 사이트도 수집됩니다.
echo    이 창은 한 번만 실행하면 됩니다.
echo    아무 키나 누르면 닫혀요.
echo  ====================================================
pause >nul
