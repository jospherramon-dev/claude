# ============================================================
#   app_escritorio.py — App de escritorio (ventana propia)
#
#   Qué hace, en orden:
#     1) Busca actualizaciones en tu repo de GitHub. Si hay una
#        versión nueva, la descarga, la aplica y reinicia la app
#        (todo automático, sin tocar tu carpeta datos/).
#     2) Levanta el servidor local del bot (127.0.0.1:5000).
#     3) Abre el panel en una VENTANA de escritorio propia
#        (no en el navegador).
#
#   Este es el archivo que abre el acceso directo del escritorio.
# ============================================================

import os
import sys
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = 5000


def _log(msg):
    print(msg, flush=True)


# ── 1) Auto-actualización (antes de cargar el bot) ───────────
def _comprobar_actualizacion():
    try:
        import actualizador
    except Exception as e:
        _log(f"[UPDATE] Actualizador no disponible: {e}")
        return
    # Si aplica una actualización, reinicia el proceso y no vuelve aquí.
    actualizador.actualizar_si_hace_falta(logger=_log)


# ── 2) Servidor local del bot ────────────────────────────────
def _puerto_ocupado(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _iniciar_servidor():
    from http.server import ThreadingHTTPServer
    import servidor  # importa bot y carga la config
    srv = ThreadingHTTPServer((HOST, PORT), servidor.Handler)
    srv.serve_forever()


def _esperar_servidor(host, port, timeout=15):
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
        # Sin pywebview: abrimos en el navegador como plan B para no dejar
        # al usuario tirado.
        _log("[VENTANA] pywebview no está instalado; abro en el navegador.")
        import webbrowser
        webbrowser.open(url)
        # Mantenemos el proceso vivo para que el servidor siga corriendo.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

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
    _comprobar_actualizacion()

    url = f"http://{HOST}:{PORT}"

    if _puerto_ocupado(HOST, PORT):
        # Ya hay una instancia corriendo: solo abrimos la ventana hacia ella.
        _log("[APP] El servidor ya estaba en marcha; abro la ventana.")
    else:
        hilo = threading.Thread(target=_iniciar_servidor, daemon=True)
        hilo.start()
        if not _esperar_servidor(HOST, PORT):
            _log("[APP] El servidor no arrancó a tiempo. Revisa la consola.")

    _abrir_ventana(url)


if __name__ == "__main__":
    main()
