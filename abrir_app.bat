@echo off
title BOT JPH TRADING
cd /d "%~dp0"

:: Si pywebview esta disponible -> ventana limpia sin consola.
:: Si NO (habitual en Python muy nuevo) -> consola visible para que
:: veas los mensajes y cualquier error, y navegador.
python -c "import webview" >nul 2>&1
if errorlevel 1 (
    color 0A
    echo.
    echo  ============================================
    echo    BOT JPH TRADING - iniciando...
    echo    (Deja esta ventana abierta mientras operas)
    echo  ============================================
    echo.
    python app_escritorio.py
    echo.
    echo  --------------------------------------------
    echo  Si viste un error arriba, mandame una foto o
    echo  el archivo:  datos\arranque.log
    echo  --------------------------------------------
    pause
) else (
    start "" pythonw app_escritorio.py
)
