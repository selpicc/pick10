@echo off
title 셀픽 작업창(안전)

REM ============================================================
REM  셀픽 작업창 (안전)
REM  클릭하면 Windows Terminal(안 꺼지는 창)에서
REM  Claude Code가 프로젝트 폴더로 열립니다.
REM ============================================================

set "PROJ=C:\Users\DS006\Documents\Claude\Projects\셀픽 영업처 수집\셀픽 영업처 수집"

REM 앱 별칭 폴더(WindowsApps)를 PATH 앞에 추가 → wt 별칭이 항상 잡힘(재부팅 불필요)
set "PATH=%LOCALAPPDATA%\Microsoft\WindowsApps;%PATH%"

REM wt(Windows Terminal)를 이름으로 실행. 프로젝트 폴더에서 Claude Code 시작.
start "" wt -d "%PROJ%" cmd /k claude

exit /b
