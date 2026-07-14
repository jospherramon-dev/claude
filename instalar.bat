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
:: Usamos --user (instala en tu carpeta, sin permisos de administrador) y
:: --no-cache-dir (evita el error de permisos en la cache de pip que
:: aparece en muchos Windows). Instalamos el NUCLEO del bot por separado
:: de pywebview: si pywebview no se puede instalar (p.ej. en Python muy
:: nuevo), el bot funciona igual y la app abre en el navegador.
echo  Actualizando pip...
python -m pip install --upgrade pip --user --no-cache-dir --quiet --disable-pip-version-check >nul 2>&1

echo  Instalando el nucleo (requests + websocket-client)...
echo  [necesario para todo: panel, Deriv y actualizaciones]
python -m pip install --user --no-cache-dir --disable-pip-version-check requests "websocket-client>=1.0"
if errorlevel 1 (
    echo.
    echo  Reintentando sin --user...
    python -m pip install --no-cache-dir --disable-pip-version-check requests "websocket-client>=1.0"
)
if errorlevel 1 (
    echo.
    echo  [ERROR] No pude instalar el nucleo del bot.
    echo          Prueba abrir esta ventana como Administrador, o revisa tu conexion.
    pause
    exit /b 1
)
echo  [OK] Nucleo instalado.
echo.

echo  Instalando soporte de IQ Option (opcional)...
python -m pip install --user --no-cache-dir --disable-pip-version-check iqoptionapi >nul 2>&1
if errorlevel 1 (
    echo  [AVISO] No pude instalar iqoptionapi (puede fallar en Python muy nuevo).
    echo          Podras usar DERIV (sinteticos) igual; IQ Option quedaria no
    echo          disponible hasta instalarla. Si la necesitas, usa Python 3.12.
) else (
    echo  [OK] IQ Option disponible.
)
echo.

echo  Instalando la ventana de escritorio pywebview (opcional)...
python -m pip install --user --no-cache-dir --disable-pip-version-check "pywebview>=4.0" >nul 2>&1
if errorlevel 1 (
    echo  [AVISO] No pude instalar pywebview (habitual en Python muy nuevo).
    echo          No pasa nada: la app abrira en tu NAVEGADOR en vez de una
    echo          ventana propia. Todo lo demas funciona igual.
) else (
    echo  [OK] Ventana de escritorio lista.
)
echo.

:: --- 4) Crear acceso directo en el Escritorio ---------------------------
:: El acceso directo abre abrir_app.bat, que elige solo entre ventana
:: limpia (si hay pywebview) o consola visible con logs (si no).
set "APPDIR=%~dp0"
set "TARGET=%APPDIR%abrir_app.bat"
set "ARGS="
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
