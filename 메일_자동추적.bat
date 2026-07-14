@echo off
REM ------------------------------------------------------------------
REM  Selpic - daily mail tracking (Windows Task Scheduler)
REM    1) check Gmail: sent / replied -> update sales status
REM    2) create follow-up DRAFTS after 7 days no-reply (never sends)
REM
REM  This file MUST be saved as cp949 (Korean filenames below).
REM  Register:  venv\Scripts\python 메일_자동추적_등록.py
REM ------------------------------------------------------------------
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo.>> "메일_추적_로그.txt"
echo ==================================================>> "메일_추적_로그.txt"
echo [%date% %time%] start>> "메일_추적_로그.txt"

venv\Scripts\python.exe "메일_추적.py" --followup >> "메일_추적_로그.txt" 2>&1

echo [%date% %time%] done (exit %errorlevel%)>> "메일_추적_로그.txt"
