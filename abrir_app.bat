@echo off
color 0A
title BOT JPH TRADING
cd /d "%~dp0"

:: Marca de que corremos dentro del bucle de este .bat: asi el actualizador
:: pide reinicio con codigo de salida 10 (limpio) en vez de lanzar otro proceso.
set "BOT_IFC_LOOP=1"

:reinicio
echo.
echo  ===================================================
echo    BOT JPH TRADING - iniciando...
echo    (Deja esta ventana abierta mientras el bot opera)
echo  ===================================================
echo.

python app_escritorio.py
set "RC=%errorlevel%"

if "%RC%"=="10" (
    echo.
    echo  Reiniciando tras actualizacion...
    goto reinicio
)

echo.
echo  ===================================================
echo    La app termino o se cerro (codigo %RC%).
echo    Esto es el registro de arranque:
echo  ===================================================
if exist "datos\arranque.log" (
    type "datos\arranque.log"
) else (
    echo   [!] No se creo datos\arranque.log
    echo       Eso significa que Python ni siquiera arranco.
    echo       Comprueba: escribe   python --version   aqui abajo.
)
echo.
echo  ===================================================
echo    Copiame TODO lo de arriba por el chat.
echo  ===================================================
echo.
pause
