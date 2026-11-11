@echo off
setlocal
cd /d "%~dp0"
title Diagnostico 3 del area de Produccion
echo.
echo Consultando indicadores de Produccion en Odoo (puede tardar unos minutos) ...
echo.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo Python no esta instalado. Ejecute primero 1_instalar.bat
  pause
  exit /b 1
)
%PY% produccion_diagnostico3.py > diagnostico_produccion3.txt 2>&1
type diagnostico_produccion3.txt
echo.
echo Listo. El resultado quedo en diagnostico_produccion3.txt
pause
