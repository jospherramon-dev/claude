# ============================================================
#   app_escritorio.py — App de escritorio (ventana propia)
#
#   Qué hace, en orden:
#     1) Busca actualizaciones en tu repo de GitHub. Si hay una
#        versión nueva, la descarga, la aplica y reinicia la app.
#     2) Levanta el servidor local del bot (127.0.0.1:5000).
#     3) Abre el panel en una VENTANA de escritorio propia
#        (si pywebview está instalado) o, si no, en el navegador.
#
#   TODO lo que pasa al arrancar se guarda en datos/arranque.log
#   y, si algo falla, se abre una página de error en el navegador
#   en vez de cerrarse en silencio.
# ============================================================

import os
import sys
import socket
import threading
import time
import traceback
import datetime

HOST = "127.0.0.1"
PORT = 5000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "datos", "arranque.log")


def _log(msg):
    linea = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print(linea, flush=True)
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def _mostrar_error(titulo, detalle):
    """Escribe una página de error legible y la abre en el navegador, para
    que el usuario VEA qué pasó en vez de una ventana que se cierra sola."""
    _log(f"ERROR FATAL: {titulo}\n{detalle}")
    try:
        ruta = os.path.join(BASE_DIR, "datos", "error_arranque.html")
        html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>BOT JPH TRADING — error al arrancar</title>
<style>body{{background:#0f1720;color:#e6edf3;font-family:system-ui,Segoe UI,sans-serif;padding:32px;line-height:1.6}}
h1{{color:#f85149}} pre{{background:#182533;border:1px solid #2a3a4d;border-radius:10px;padding:16px;
white-space:pre-wrap;word-break:break-word;font-size:13px}} b{{color:#58a6ff}}</style></head>
<body><h1>No pude arrancar la app</h1>
<p>{titulo}</p>
<p>Copia todo el recuadro de abajo y pásamelo por el chat — con eso lo arreglo:</p>
<pre>{detalle}</pre>
<p style="color:#8aa0b6">Este mismo detalle quedó guardado en <b>datos/arranque.log</b>.</p>
</body></html>"""
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(html)
        import webbrowser
        webbrowser.open("file:///" + ruta.replace("\\", "/"))
    except Exception:
        pass


# ── 1) Auto-actualización (antes de cargar el bot) ───────────
def _comprobar_actualizacion():
    try:
        import actualizador
    except Exception as e:
        _log(f"[UPDATE] Actualizador no disponible: {e}")
        return
    try:
        actualizador.actualizar_si_hace_falta(logger=_log)
    except Exception:
        _log("[UPDATE] Error comprobando actualizaciones (no bloquea):\n" + traceback.format_exc())


# ── 2) Servidor local del bot ────────────────────────────────
def _puerto_ocupado(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _esperar_servidor(host, port, timeout=20):
    fin = time.time() + timeout
    while time.time() < fin:
        if _puerto_ocupado(host, port):
            return True
        time.sleep(0.2)
    return False


# ── 3) Ventana de escritorio ─────────────────────────────────
def _abrir_ventana(url):
    try:
        import webview
    except Exception:
        # Sin pywebview (habitual en Python muy nuevo): abrimos en el
        # navegador. La app sigue corriendo igual.
        _log("[VENTANA] pywebview no está instalado; abro en el navegador: " + url)
        import webbrowser
        webbrowser.open(url)
        _log("[VENTANA] Navegador abierto. Deja esta ventana/proceso vivo mientras operas.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    _log("[VENTANA] Abriendo ventana de escritorio (pywebview).")
    webview.create_window(
        "BOT JPH TRADING",
        url,
        width=1180,
        height=820,
        min_size=(900, 640),
        confirm_close=True,
    )
    webview.start()   # bloquea hasta que se cierra la ventana


def main():
    _log("==================== Arranque BOT JPH TRADING ====================")
    _log(f"Python {sys.version.split()[0]} | carpeta: {BASE_DIR}")

    _comprobar_actualizacion()

    url = f"http://{HOST}:{PORT}"

    if _puerto_ocupado(HOST, PORT):
        _log("[APP] El servidor ya estaba en marcha; abro la ventana.")
    else:
        # Importamos el servidor AQUÍ (hilo principal) para que, si el bot
        # no importa (p.ej. falta una dependencia), veamos el error de
        # verdad en vez de que el hilo muera en silencio.
        try:
            from http.server import ThreadingHTTPServer
            import servidor
        except Exception as e:
            _mostrar_error(
                "Falló al cargar el motor del bot (import de servidor/bot).",
                traceback.format_exc())
            return

        def _serve():
            try:
                srv = ThreadingHTTPServer((HOST, PORT), servidor.Handler)
                _log(f"[APP] Servidor escuchando en {url}")
                srv.serve_forever()
            except Exception:
                _log("[APP] ERROR en el servidor:\n" + traceback.format_exc())

        threading.Thread(target=_serve, daemon=True).start()

        if not _esperar_servidor(HOST, PORT):
            _mostrar_error(
                "El servidor no respondió a tiempo tras arrancar.",
                "Revisa datos/arranque.log. Puede ser un antivirus/firewall "
                "bloqueando el puerto 5000, o un error del motor arriba.")
            return

    _abrir_ventana(url)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _mostrar_error("Error inesperado al arrancar.", traceback.format_exc())
