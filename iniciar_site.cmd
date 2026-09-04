@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Preparando o Come Doce pela primeira vez...
  where py >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Python nao foi encontrado. Instale o Python 3 e marque a opcao Add Python to PATH.
    pause
    exit /b 1
  )
  set "PYTHON_SELECTOR="
  py -3.14 -c "import sys" >nul 2>nul && set "PYTHON_SELECTOR=-3.14"
  if not defined PYTHON_SELECTOR py -3.13 -c "import sys" >nul 2>nul && set "PYTHON_SELECTOR=-3.13"
  if not defined PYTHON_SELECTOR py -3.12 -c "import sys" >nul 2>nul && set "PYTHON_SELECTOR=-3.12"
  if not defined PYTHON_SELECTOR py -3.11 -c "import sys" >nul 2>nul && set "PYTHON_SELECTOR=-3.11"
  if not defined PYTHON_SELECTOR (
    echo.
    echo E necessario ter Python 3.11 ou mais recente instalado.
    pause
    exit /b 1
  )
  py %PYTHON_SELECTOR% -m venv "%~dp0.venv"
  if errorlevel 1 goto :erro
)

"%VENV_PYTHON%" -c "import fastapi, uvicorn, jinja2, sqlalchemy, multipart, argon2, itsdangerous, email_validator, PIL, numpy, cv2, httpx, dotenv, pytesseract" >nul 2>nul
if errorlevel 1 (
  echo Instalando dependencias...
  "%VENV_PYTHON%" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto :erro
)

echo.
echo Iniciando o Come Doce...
echo Site: http://127.0.0.1:8000
echo Para encerrar, pressione Ctrl+C.
echo.
"%VENV_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
exit /b %errorlevel%

:erro
echo.
echo Nao foi possivel preparar o site. Verifique a instalacao do Python e a conexao com a internet.
pause
exit /b 1
