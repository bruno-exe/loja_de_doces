@echo off
setlocal
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d "%~dp0"
call npm run android:sync
if errorlevel 1 (
  echo.
  echo Falha ao sincronizar o projeto Android.
  pause
  exit /b 1
)
call npm run android:open
endlocal
