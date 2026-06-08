@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo    PICK10 - Selpic Sales Dashboard
echo ============================================
echo.
echo  Starting the dashboard...
echo.
echo  [ Local  ]  http://localhost:8501
echo  [ Network]  see "Network URL" printed below
echo              (same Wi-Fi users can open that address)
echo.
echo  To stop: close this window or press Ctrl + C
echo ============================================
echo.
venv\Scripts\streamlit run dashboard.py
echo.
echo  (dashboard stopped)
pause
