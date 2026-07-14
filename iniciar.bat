@echo off
title BOT JPH TRADING
color 0A

:: Ir a la carpeta donde esta este mismo archivo
cd /d "%~dp0"

echo.
echo  ============================================
echo    BOT JPH TRADING - Iniciando (modo navegador)...
echo    Carpeta: %~dp0
echo  ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python no esta instalado.
    echo  Descargalo en https://www.python.org/downloads/
    pause
    exit /b
)

echo  Instalando dependencias...
pip install -r requirements.txt --quiet --disable-pip-version-check
echo  Listo.
echo.
echo  Abriendo dashboard en tu navegador...
echo  Deja esta ventana abierta mientras el bot opera.
echo.

python servidor.py

pause
