# ============================================================
#   servidor.py — SIN Flask, solo librerías de Python puro
#   Compatible con Python 3.6+
#
#   NOVEDAD: incluye un DIAGNÓSTICO de pares normales integrado.
#   Abre  http://localhost:5000/diagnostico  con el bot ya
#   conectado (dale a "Iniciar bot" primero). Reutiliza la misma
#   conexión del bot, así que NO necesita instalar ni ejecutar nada
#   aparte — corre con el mismo Python que el bot.
# ============================================================

import os, json, threading, webbrowser, secrets
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import bot

# ── Token CSRF de sesión (defendemos POST contra CSRF en localhost) ──────
# Se genera una vez al arrancar y se sirve vía /api/estado (campo csrf_token).
# La UI debe enviarlo en el header X-CSRF-Token en cada POST; si no coincide,
# el POST se rechaza. Asi una web maliciosa que visites no puede llamar a
# /api/iniciar / /api/guardar_config / etc. sin antes leer el token (que solo
# esta pagina sirve, mismo origen).
_SESSION_CSRF_TOKEN = secrets.token_hex(16)

bot.cargar_config()

# ── HTML del dashboard (leído una sola vez al arrancar) ──────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "ui", "index.html")
with open(_HTML_PATH, "rb") as _f:
    _HTML = _f.read()


# ============================================================
#   DIAGNÓSTICO DE PARES NORMALES (usa la conexión viva del bot)
# ============================================================
def _diagnostico_pares():
    """Consulta get_all_open_time() usando la conexión del bot (bot._Iq).
    NO abre ninguna operación. Devuelve un dict con el resultado o un
    error legible."""
    Iq = bot._Iq
    if Iq is None:
        return {"ok": False, "error":
                "El bot todavía no está conectado. En la pestaña del bot, pon tu usuario y "
                "contraseña y dale a 'Iniciar bot'. Cuando el estado diga 'Conectado', "
                "recarga esta página."}
    try:
        conectado = Iq.check_connect()
    except Exception:
        conectado = True   # si el chequeo falla, igual intentamos consultar
    if not conectado:
        return {"ok": False, "error":
                "El bot está iniciado pero aún no conectado. Espera a que diga 'Conectado' "
                "y recarga esta página."}

    # Fuente FIABLE del bot (get_all_init_v2), la misma que usan los pares
    # normales. por_nombre = {nombre_real: {"id","binary","turbo"}}.
    por_nombre, hubo_fallo = bot.obtener_estado_binarios()
    if hubo_fallo:
        return {"ok": False, "error":
                "No pude leer el estado de instrumentos (get_all_init_v2 no respondió). "
                "Espera unos segundos con el bot ya conectado y dale a 'Volver a ejecutar'."}

    deseados = bot.config.get("pares_normales") or ["EURUSD", "GBPUSD", "USDJPY", "EURGBP", "AUDUSD", "USDCHF"]
    pares_estado = []
    for par in deseados:
        encontrado = None
        for cand in (par, par + "-op"):   # aceptamos nombre pedido y variante -op
            if cand in por_nombre:
                encontrado = cand
                break
        if encontrado:
            info = por_nombre[encontrado]
            pares_estado.append({
                "par": par, "encontrado": True, "nombre_real": encontrado,
                "id": info.get("id"),
                "binary": bool(info.get("binary")), "turbo": bool(info.get("turbo")),
            })
        else:
            pares_estado.append({
                "par": par, "encontrado": False, "nombre_real": None,
                "id": None, "binary": False, "turbo": False,
            })

    no_otc = sorted(n for n in por_nombre.keys() if not str(n).endswith("-OTC"))
    otc    = sorted(n for n in por_nombre.keys() if str(n).endswith("-OTC"))

    return {"ok": True, "modo": bot.config.get("modo", "?"),
            "pares_estado": pares_estado,
            "nombres_no_otc": no_otc[:100],
            "cuantos_otc": len(otc)}


_DIAG_HTML = b"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnostico - Pares normales</title>
<style>
 :root{--bg:#0f1720;--bg2:#182533;--border:#2a3a4d;--text:#e6edf3;--muted:#8aa0b6;--green:#3fb950;--red:#f85149;--accent:#58a6ff;}
 *{box-sizing:border-box;} body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,Segoe UI,Roboto,sans-serif;padding:24px;}
 .wrap{max-width:820px;margin:0 auto;}
 h1{font-size:20px;margin:0 0 4px;} .sub{color:var(--muted);font-size:13px;margin:0 0 18px;}
 .card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px;}
 button{background:var(--accent);color:#08131f;border:0;border-radius:8px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
 button.sec{background:var(--bg);color:var(--text);border:1px solid var(--border);}
 .chip{display:inline-block;background:var(--bg);border:1px solid var(--border);border-radius:99px;padding:4px 10px;font-size:12px;margin:3px;}
 table{width:100%;border-collapse:collapse;font-size:13px;} td,th{text-align:left;padding:7px 8px;border-bottom:1px solid var(--border);}
 th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;}
 .ok{color:var(--green);font-weight:700;} .no{color:var(--red);font-weight:700;} .gris{color:var(--muted);}
 .err{background:rgba(248,81,73,.12);border:1px solid var(--red);color:#ffb3ae;padding:12px 14px;border-radius:10px;font-size:14px;}
 details{margin-top:8px;} summary{cursor:pointer;color:var(--accent);font-size:13px;}
 .names{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--muted);word-break:break-word;margin-top:6px;line-height:1.6;}
 .spin{color:var(--muted);font-size:14px;} .hint{color:var(--muted);font-size:12px;margin-top:10px;}
</style></head>
<body><div class="wrap">
 <h1>Diagnostico - Pares normales</h1>
 <p class="sub">Consulta (sin operar) que instrumentos reporta IQ Option como abiertos. Usa la conexion del bot.</p>
 <div class="card"><b>Antes de nada:</b> en la pestana del bot, dale a <b>"Iniciar bot"</b> y espera a que diga <b>Conectado</b>. Luego pulsa "Volver a ejecutar" aqui abajo.</div>
 <div id="out"></div>
 <div class="card"><button onclick="run()">Volver a ejecutar</button> <button class="sec" onclick="copiar()">Copiar resultado</button><div class="hint">Copia el resultado y pasamelo por el chat.</div></div>
</div>
<script>
async function run(){
 const out = document.getElementById('out');
 out.innerHTML = '<div class="card spin">Consultando a IQ Option...</div>';
 let data;
 try{ const r = await fetch('/api/diagnostico_pares'); data = await r.json(); }
 catch(e){ out.innerHTML = '<div class="card"><div class="err">Error de red: '+e+'</div></div>'; return; }
 if(!data.ok){ out.innerHTML = '<div class="card"><div class="err">'+(data.error||'Error')+'</div></div>'; window.__diag=data; return; }
 let h='';
 h += '<div class="card">Conectado en modo <b>'+data.modo+'</b>. Estado para opciones binarias/turbo (lo que opera el bot).</div>';
 h += '<div class="card"><b>Estado de cada par normal</b><table style="margin-top:8px;"><tr><th>Par pedido</th><th>Nombre real</th><th>Binary</th><th>Turbo</th></tr>';
 for(const pe of data.pares_estado){
   if(!pe.encontrado){
     h += '<tr><td><b>'+pe.par+'</b></td><td colspan="3"><span class="gris">no aparece</span></td></tr>';
   } else {
     const b = pe.binary ? '<span class="ok">ABIERTO</span>' : '<span class="no">cerrado</span>';
     const t = pe.turbo ? '<span class="ok">ABIERTO</span>' : '<span class="no">cerrado</span>';
     const nr = pe.nombre_real + (pe.id!=null ? ' <span class="gris">(id '+pe.id+')</span>' : '');
     h += '<tr><td><b>'+pe.par+'</b></td><td>'+nr+'</td><td>'+b+'</td><td>'+t+'</td></tr>';
   }
 }
 h += '</table></div>';
 h += '<div class="card"><b>Pares NO-OTC disponibles ahora</b> ('+data.nombres_no_otc.length+')<div class="hint">Nombres reales que ofrece tu cuenta para binarias/turbo, sin contar los -OTC (hay '+data.cuantos_otc+' -OTC). Si tu par existe pero con otro nombre, aqui se ve.</div><div class="names" style="margin-top:8px;">'+(data.nombres_no_otc.join(', ')||'(ninguno: seguramente el mercado normal esta cerrado y solo hay OTC)')+'</div></div>';
 out.innerHTML = h; window.__diag = data;
}
function copiar(){
 if(!window.__diag){ alert('Ejecuta primero.'); return; }
 navigator.clipboard.writeText(JSON.stringify(window.__diag, null, 2)).then(()=>alert('Resultado copiado. Pegamelo en el chat.'));
}
run();
</script>
</body></html>"""


_BACKTEST_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest IFC</title>
<style>
 :root{--bg:#0f1720;--bg2:#182533;--border:#2a3a4d;--text:#e6edf3;--muted:#8aa0b6;--green:#3fb950;--red:#f85149;--accent:#58a6ff;}
 *{box-sizing:border-box;} body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,Segoe UI,Roboto,sans-serif;padding:24px;}
 .wrap{max-width:760px;margin:0 auto;}
 h1{font-size:20px;margin:0 0 4px;} .sub{color:var(--muted);font-size:13px;margin:0 0 18px;}
 .card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px;}
 label{font-size:11px;color:var(--muted);display:block;margin-bottom:3px;}
 select,input{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px 10px;font-size:13px;}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:end;}
 button{background:var(--accent);color:#08131f;border:0;border-radius:8px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;} td,th{text-align:left;padding:8px;border-bottom:1px solid var(--border);}
 th{color:var(--muted);font-size:11px;text-transform:uppercase;}
 .win{color:var(--green);font-weight:700;} .lose{color:var(--red);font-weight:700;}
 .big{font-size:22px;font-weight:800;}
 .err{background:rgba(248,81,73,.12);border:1px solid var(--red);color:#ffb3ae;padding:12px;border-radius:10px;}
 .hint{color:var(--muted);font-size:12px;margin-top:8px;} .spin{color:var(--muted);}
</style></head>
<body><div class="wrap">
 <h1>Backtest IFC — sobre tus velas OTC reales</h1>
 <p class="sub">Corre la estrategia sobre el histórico real del par. Compara los dos modos: continuidad vs reversión.</p>
 <div class="card"><b>Antes:</b> dale a <b>Iniciar bot</b> en la otra pestaña y espera a <b>Conectado</b>. Traer el histórico tarda unos segundos.</div>
 <div class="card">
  <div class="row">
   <div><label>Estrategia</label><select id="estrategia"><option value="ifc">IFC (indecisi&oacute;n-fuerza)</option><option value="ifcpro">IFC-Pro (rompe nivel + retroceso)</option><option value="nr">NR (N&uacute;mero Redondo - El Hermoso)</option><option value="facundo">Facundo (price action en S/R)</option></select></div>
   <div><label>Par</label><select id="par"></select></div>
   <div><label>Velas históricas</label><input id="velas" type="number" value="3000" min="500" max="10000" style="width:110px"></div>
   <button onclick="run()">Ejecutar backtest</button>
  </div>
  <div class="hint">Break-even con payout 87% = 53.5%. Cuanto más velas, más fiable (pero tarda más).</div>
 </div>
 <div id="out"></div>
</div>
<script>
const OTC_TODOS=['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','EURGBP-OTC','EURJPY-OTC','GBPJPY-OTC','AUDUSD-OTC','USDCAD-OTC','USDCHF-OTC','NZDUSD-OTC','AUDCAD-OTC','AUDCHF-OTC','CADJPY-OTC','CHFJPY-OTC','EURCAD-OTC','EURCHF-OTC','EURAUD-OTC','GBPAUD-OTC','GBPCAD-OTC','GBPCHF-OTC'];
async function cargarPares(){
 const sel=document.getElementById('par'); const vistos={};
 let extra=[];
 try{ const r=await fetch('/api/config'); const c=await r.json(); extra=(c.pares||[]).concat(c.pares_normales||[]);
      const se=document.getElementById('estrategia'); if(se && c.estrategia) se.value=c.estrategia; }catch(e){}
 OTC_TODOS.concat(extra).forEach(p=>{ if(!vistos[p]){vistos[p]=1; const o=document.createElement('option'); o.value=p;o.textContent=p; sel.appendChild(o);} });
}
function tabla(nombre, st, be){
 const wr=st.winrate;
 const cls = wr>=be ? 'win':'lose';
 return '<div class="card"><b>'+nombre+'</b>'+
   '<div class="big '+cls+'">'+wr+'%</div>'+
   '<table><tr><th>Ops</th><th>Ganadas</th><th>Perdidas</th><th>Compras</th><th>Ventas</th><th>Peor racha</th></tr>'+
   '<tr><td>'+st.total+'</td><td class="win">'+st.wins+'</td><td class="lose">'+st.losses+'</td>'+
   '<td>'+st.buys+' ('+st.wr_buy+'%)</td><td>'+st.sells+' ('+st.wr_sell+'%)</td><td>'+st.peor_racha_perdidas+'</td></tr></table></div>';
}
async function run(){
 const out=document.getElementById('out');
 const par=document.getElementById('par').value;
 const velas=document.getElementById('velas').value;
 const estr=document.getElementById('estrategia').value;
 if(!par){ out.innerHTML='<div class="card"><div class="err">Elige un par.</div></div>'; return; }
 out.innerHTML='<div class="card spin">Trayendo '+velas+' velas de '+par+' y corriendo el backtest de '+estr.toUpperCase()+'… (puede tardar)</div>';
 let d;
 try{ const r=await fetch('/api/backtest?par='+encodeURIComponent(par)+'&velas='+velas+'&estrategia='+encodeURIComponent(estr)); d=await r.json(); }
 catch(e){ out.innerHTML='<div class="card"><div class="err">Error de red: '+e+'</div></div>'; return; }
 if(!d.ok){ out.innerHTML='<div class="card"><div class="err">'+(d.error||'Error')+'</div></div>'; return; }
 let h='<div class="card">Estrategia <b>'+(d.estrategia||'').toUpperCase()+'</b> · Par <b>'+d.par+'</b> · '+d.velas+' velas · break-even '+d.break_even_87+'%</div>';
 (d.modos||[]).forEach(m=>{ h+=tabla('Modo '+m.nombre, m, d.break_even_87); });
 h+='<div class="card hint">Verde = supera el break-even. Para operar la ganadora en vivo: config <b>estrategia</b> ("ifc"/"ifcpro"/"nr"/"facundo") y su modo (<b>ifc_modo</b>, <b>nr_modo</b> o <b>facundo_modo</b>). Pocas ops = poca certeza; corre varios miles de velas y varios pares.</div>';
 out.innerHTML=h;
}
cargarPares();
</script>
</body></html>"""


# ── Manejador HTTP ───────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    # ─ Silenciar logs de acceso en consola ──────────────────
    def log_message(self, format, *args):
        pass

    # ─ GET ──────────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._send(200, "text/html; charset=utf-8", _HTML)

        elif path == "/diagnostico":
            self._send(200, "text/html; charset=utf-8", _DIAG_HTML)

        elif path == "/api/diagnostico_pares":
            self._json(200, _diagnostico_pares())

        elif path == "/api/pares_disponibles":
            # Lista OTC + normales abiertos con payout actual y modo IFC
            # configurado para cada par (para el modo manual).
            self._json(200, bot.detectar_pares_disponibles())

        elif path == "/api/estadisticas":
            # Estadísticas desglosadas (JSON) para la pestaña de la UI.
            self._json(200, bot.calcular_estadisticas())

        elif path == "/api/descargar_excel":
            # Genera el .xlsx de estadísticas y lo entrega como descarga.
            data, nombre = bot.generar_excel_estadisticas()
            if data is None:
                self._json(200, {"ok": False, "error": nombre})
            else:
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5000")
                self.end_headers()
                self.wfile.write(data)

        elif path == "/api/descargar_csv":
            # Entrega el historial permanente crudo como descarga .csv.
            ruta = bot.HISTORIAL_CSV
            if not os.path.exists(ruta):
                self._json(200, {"ok": False,
                                 "error": "Todavía no hay operaciones registradas."})
            else:
                with open(ruta, "rb") as f:
                    data = f.read()
                nombre = f"historial_operaciones_{__import__('datetime').date.today().isoformat()}.csv"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5000")
                self.end_headers()
                self.wfile.write(data)

        elif path == "/backtest":
            self._send(200, "text/html; charset=utf-8", _BACKTEST_HTML)

        elif path == "/api/backtest":
            qs = parse_qs(urlparse(self.path).query)
            par = (qs.get("par", [""])[0] or "").strip()
            estrategia = (qs.get("estrategia", [""])[0] or "").strip() or None
            try:
                velas = int(qs.get("velas", ["3000"])[0])
            except Exception:
                velas = 3000
            velas = max(200, min(velas, 10000))
            if not par:
                self._json(200, {"ok": False, "error": "Falta el par."})
            else:
                self._json(200, bot.backtest_estrategia(par, velas, estrategia))

        elif path == "/api/estado":
            est = bot.get_estado()
            # Inyectamos el token CSRF de sesión para que la UI lo incluya
            # en cada POST siguiente.
            est["csrf_token"] = _SESSION_CSRF_TOKEN
            self._json(200, est)

        elif path == "/api/config":
            # SECURITY: NUNCA devolver la contraseña al navegador.
            # Devolvemos una copia con password enmascarada y un flag.
            cfg_public = dict(bot.config)
            pwd = cfg_public.get("password", "")
            cfg_public["password"] = ""
            cfg_public["has_password"] = bool(pwd)
            # El token de Deriv también es secreto: no lo devolvemos.
            tok = cfg_public.get("deriv_token", "")
            cfg_public["deriv_token"] = ""
            cfg_public["has_deriv_token"] = bool(tok)
            self._json(200, cfg_public)

        else:
            self._send(404, "text/plain", b"Not found")

    # ─ POST ─────────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()

        # SECURITY: todos los POST requieren token CSRF (excepto los que
        # no mutan estado: ninguno). La UI lee el token de /api/estado y
        # lo manda en X-CSRF-Token.
        token_recibido = self.headers.get("X-CSRF-Token", "")
        if token_recibido != _SESSION_CSRF_TOKEN:
            self._json(403, {"ok": False, "error": "CSRF token invalido. Recarga la pagina."})
            return

        if path == "/api/iniciar":
            if data:
                bot.set_config(data)
            faltan = bot._faltan_credenciales(bot.config)
            if faltan:
                self._json(200, {"ok": False, "error": faltan})
                return
            bot.iniciar_bot()
            self._json(200, {"ok": True})

        elif path == "/api/detener":
            bot.detener_bot()
            self._json(200, {"ok": True})

        elif path == "/api/guardar_config":
            if data:
                bot.set_config(data)
            self._json(200, {"ok": True})

        elif path == "/api/backtest_dia":
            # Corre backtest IFC (ambos modos) sobre las últimas 1440 velas
            # M1 de cada par pedido y devuelve la sugerencia. Payload:
            # {"pares": ["USDCHF-OTC", "GBPCHF-OTC", ...], "velas": 1440}
            pares = list((data or {}).get("pares") or [])
            velas = int((data or {}).get("velas") or 1440)
            velas = max(200, min(velas, 5000))
            self._json(200, bot.backtest_pares_1dia(pares, velas))

        elif path == "/api/modo_par":
            # Asigna el modo IFC (continuidad/reversion) a un par. Payload:
            # {"par": "USDCHF-OTC", "modo": "continuidad"|"reversion"|null}
            par  = (data or {}).get("par", "")
            modo = (data or {}).get("modo")
            if not par:
                self._json(200, {"ok": False, "error": "Falta el par."})
            else:
                self._json(200, bot.set_modo_par(par, modo))

        elif path == "/api/reiniciar_dia":
            # Botón "Reiniciar día" — pone en cero todos los contadores
            # (wins/losses/neto/racha/operaciones) y borra sesion.json.
            bot.reiniciar_sesion()
            self._json(200, {"ok": True})

        elif path == "/api/test_telegram":
            # Prueba de diagnóstico de Telegram con las credenciales del
            # payload (sin guardarlas) o las de config si no vienen.
            tok = (data or {}).get("token_telegram")
            cid = (data or {}).get("chat_id")
            self._json(200, bot.probar_telegram(tok, cid))

        else:
            self._send(404, "text/plain", b"Not found")

    # ─ Helpers ──────────────────────────────────────────────
    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw)
        except Exception:
            return {}

    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # SECURITY: NO abrimos CORS a '*'. El servidor solo se sirve en
        # 127.0.0.1; permitir cualquier origen exponía el bot a CSRF desde
        # webs maliciosas. Si alguna vez necesitas cross-origin, sé
        # explicito sobre el origen permitido.
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5000")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body)

# ── Arranque ─────────────────────────────────────────────────
if __name__ == "__main__":
    HOST, PORT = "127.0.0.1", 5000
    # ThreadingHTTPServer: cada request en su propio hilo. Sin esto, un
    # backtest masivo (20 pares × 1440 velas) bloquearía TODOS los demás
    # requests — el polling de estado y la UI entera se congelarían
    # durante minutos. Las funciones del bot ya son thread-safe (_lock).
    server = ThreadingHTTPServer((HOST, PORT), Handler)

    print("\n" + "=" * 45)
    print("  Bot IFC Auto — servidor iniciado (sin Flask)")
    print(f"  Abre tu navegador en: http://localhost:{PORT}")
    print(f"  Diagnostico de pares: http://localhost:{PORT}/diagnostico")
    print("  Deja esta ventana abierta mientras opera.")
    print("=" * 45 + "\n")

    # Abrir navegador automáticamente tras 1.5s
    def abrir():
        import time; time.sleep(1.5)
        webbrowser.open(f"http://localhost:{PORT}")
    threading.Thread(target=abrir, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor cerrado.")
        server.server_close()
