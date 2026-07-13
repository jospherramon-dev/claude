@echo off
setlocal enabledelayedexpansion
title BOT JPH TRADING - Instalador
color 0A
cd /d "%~dp0"

echo.
echo  ==================================================
echo    BOT JPH TRADING - Instalador de la app de escritorio
echo  ==================================================
echo.

:: --- 1) Comprobar Python -------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python no esta instalado.
    echo.
    echo  Descargalo aqui:  https://www.python.org/downloads/
    echo  IMPORTANTE: al instalar, marca la casilla "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
echo  [OK] Python detectado:
python --version
echo.

:: --- 2) Instalar dependencias -------------------------------------------
echo  Instalando dependencias (requests, iqoptionapi, pywebview)...
python -m pip install --upgrade pip --quiet --disable-pip-version-check
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo  [ERROR] Fallo al instalar dependencias. Revisa tu conexion.
    pause
    exit /b 1
)
echo  [OK] Dependencias instaladas.
echo.

:: --- 3) Localizar pythonw.exe (sin ventana de consola) -------------------
for /f "delims=" %%P in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
if not exist "!PYW!" set "PYW=pythonw.exe"

:: --- 4) Crear acceso directo en el Escritorio ---------------------------
set "APPDIR=%~dp0"
set "TARGET=!PYW!"
set "ARGS=app_escritorio.py"
set "ICON="
if exist "%APPDIR%icono.ico" set "ICON=%APPDIR%icono.ico"

echo  Creando acceso directo en el Escritorio...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$viejo = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Bot IFC Auto.lnk';" ^
  "if (Test-Path $viejo) { Remove-Item $viejo }" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desk = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = $ws.CreateShortcut((Join-Path $desk 'BOT JPH TRADING.lnk'));" ^
  "$lnk.TargetPath = '!TARGET!';" ^
  "$lnk.Arguments = '!ARGS!';" ^
  "$lnk.WorkingDirectory = '%APPDIR%';" ^
  "if ('!ICON!' -ne '') { $lnk.IconLocation = '!ICON!' };" ^
  "$lnk.Description = 'BOT JPH TRADING';" ^
  "$lnk.Save();"

if errorlevel 1 (
    echo  [AVISO] No pude crear el acceso directo automaticamente.
    echo         Puedes abrir la app con:  pythonw app_escritorio.py
) else (
    echo  [OK] Acceso directo "BOT JPH TRADING" creado en tu Escritorio.
)
echo.
echo  ==================================================
echo    Listo. Ya puedes cerrar esta ventana y abrir
echo    "BOT JPH TRADING" desde el Escritorio.
echo  ==================================================
echo.
pause
