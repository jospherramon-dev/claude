# ============================================================
#   app.py — Servidor Flask (LEGACY)
#
#   ⚠️  ESTE ARCHIVO ES LEGACY. El arranque recomendado es
#      servidor.py (Python puro, sin Flask, mas seguro).
#      iniciar.bat ejecuta servidor.py, NO este.
#
#   Si por alguna razón necesitas usar app.py:
#     · Se sirve en 127.0.0.1 (NO en 0.0.0.0) para no exponer
#       credenciales a la LAN/Internet.
#     · Requiere Flask instalado: pip install flask
# ============================================================
import os, json, webbrowser, threading
from flask import Flask, render_template, request, jsonify
import bot

app = Flask(__name__)
bot.cargar_config()

# ── Páginas ──────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

# ── API ──────────────────────────────────────────────────────
@app.route("/api/estado")
def api_estado():
    return jsonify(bot.get_estado())

@app.route("/api/config")
def api_config():
    return jsonify(bot.config)

@app.route("/api/iniciar", methods=["POST"])
def api_iniciar():
    data = request.get_json()
    if not data.get("usuario") or not data.get("password"):
        return jsonify({"ok": False, "error": "Email y contraseña requeridos"})
    bot.set_config(data)
    bot.iniciar()
    return jsonify({"ok": True})

@app.route("/api/detener", methods=["POST"])
def api_detener():
    bot.detener()
    return jsonify({"ok": True})

@app.route("/api/guardar_config", methods=["POST"])
def api_guardar():
    bot.set_config(request.get_json())
    return jsonify({"ok": True})

# ── Inicio ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*45)
    print("  BOT IFC AUTO - Servidor iniciado")
    print("  Abre tu navegador en: http://localhost:5000")
    print("="*45 + "\n")
    # Abrir navegador automáticamente tras 1.5s
    def abrir():
        import time; time.sleep(1.5)
        webbrowser.open("http://localhost:5000")
    threading.Thread(target=abrir, daemon=True).start()
    # SECURITY: 127.0.0.1 SOLAMENTE. No usar 0.0.0.0 — expondría el bot
    # (con credenciales IQ Option) a toda la LAN/Internet.
    app.run(host="127.0.0.1", port=5000, debug=False)
