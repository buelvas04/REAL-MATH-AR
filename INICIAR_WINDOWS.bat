@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo No se encontro Python Launcher. Instala Python 3.11 o posterior con la opcion PATH.
  pause
  exit /b 1
)
if not exist .venv (
  py -m venv .venv
  if errorlevel 1 (echo Error creando entorno virtual & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (echo Error instalando dependencias & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo.
echo REAL-MATH AR disponible en http://127.0.0.1:8000
echo Abre esa direccion en tu navegador. Mantén esta ventana abierta.
echo.
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
