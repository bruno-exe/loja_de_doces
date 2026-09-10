@echo off
setlocal
set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"
set "PATH=%JAVA_HOME%\bin;C:\Program Files\nodejs;%PATH%"
cd /d "%~dp0"
call npm run android:sync
if errorlevel 1 goto :erro
call android\gradlew.bat -p android assembleDebug
if errorlevel 1 goto :erro
echo.
echo APK criado em:
echo %~dp0android\app\build\outputs\apk\debug\app-debug.apk
pause
exit /b 0

:erro
echo.
echo Nao foi possivel gerar o APK.
pause
exit /b 1
