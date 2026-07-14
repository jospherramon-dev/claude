# ============================================================
#   conector_deriv.py — Conector para Deriv (índices sintéticos)
#
#   Expone LOS MISMOS métodos que el bot ya usa (connect,
#   check_connect, change_balance, get_balance, get_candles, buy,
#   check_win_v3, get_all_*), pero por dentro habla con la API
#   NUEVA de Deriv (Trading API v1). Así el resto del bot no cambia.
#
#   AUTENTICACIÓN NUEVA DE DERIV (2025+):
#   Deriv reemplazó los tokens clásicos por Personal Access Tokens
#   (empiezan por "pat_"). El flujo es:
#     1) REST GET  /trading/v1/options/accounts        (lista cuentas)
#     2) REST POST /trading/v1/options/accounts/{id}/otp  (pide OTP)
#        -> devuelve una URL de WebSocket ya autenticada
#     3) Conectar a esa URL y usar los mensajes de siempre
#        (ticks_history, proposal, buy, proposal_open_contract...).
#
#   El token se crea en https://app.deriv.com/account/api-token con
#   permiso "Trade". Para PRACTICAR, usa tu cuenta DEMO: este conector
#   se NIEGA a operar en real si pediste modo Práctica, y viceversa.
# ============================================================

import json
import time
import threading

try:
    import requests
except Exception:
    requests = None

try:
    from websocket import create_connection
except Exception:
    create_connection = None

# Host REST de la API nueva de Deriv.
REST_BASE = "https://api.derivws.com"

# Índices sintéticos más comunes de Deriv. Puedes editar la lista desde
# config ("pares_deriv"). Estos están abiertos 24/7.
SIMBOLOS_DEFECTO = [
    "R_10", "R_25", "R_50", "R_75", "R_100",            # Volatility
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",  # Volatility (1s)
    "BOOM300N", "BOOM500", "BOOM1000",                  # Boom
    "CRASH300N", "CRASH500", "CRASH1000",               # Crash
    "STPRNG",                                           # Step Index
    "JD10", "JD25", "JD50", "JD75", "JD100",            # Jump
]


class ConectorDeriv:
    def __init__(self, token="", app_id="1089", modo="PRACTICE", simbolos=None,
                 payout_min=0.0, logger=None):
        self.token = (token or "").strip()
        self.app_id = str(app_id or "1089").strip()
        self.modo = modo or "PRACTICE"
        self.payout_min = float(payout_min or 0)
        self._log = logger or (lambda *_a, **_k: None)
        self._simbolos = list(simbolos) if simbolos else list(SIMBOLOS_DEFECTO)
        self.ultimo_payout_pct = None
        self._ws = None
        self._lock = threading.Lock()
        self._req_id = 0
        # Datos de la cuenta activa (se rellenan al conectar)
        self.account_id = None
        self.account_type = None      # "demo" | "real"
        self.is_virtual = None        # True si demo (compat con el resto)
        self.currency = "USD"
        self._balance = 0.0

    # ── helpers ──────────────────────────────────────────────
    def simbolos(self):
        return list(self._simbolos)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _quiere_demo(self):
        return str(self.modo).upper() in ("PRACTICE", "DEMO", "VIRTUAL")

    def _enviar(self, payload, timeout=30):
        """Envía un mensaje por el WebSocket y devuelve la respuesta con el
        mismo req_id. Ignora mensajes sueltos (suscripciones, etc.)."""
        if self._ws is None:
            raise RuntimeError("Deriv: no hay conexión abierta.")
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            msg = dict(payload)
            msg["req_id"] = rid
            self._ws.settimeout(timeout)
            self._ws.send(json.dumps(msg))
            fin = time.time() + timeout
            while time.time() < fin:
                raw = self._ws.recv()
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if data.get("req_id") == rid:
                    return data
            raise TimeoutError("Deriv: la petición no obtuvo respuesta a tiempo.")

    # ── conexión (flujo nuevo: REST -> OTP -> WebSocket) ─────
    def connect(self):
        """Devuelve (ok, motivo), igual que antes. Hace el flujo REST+OTP
        y abre el WebSocket ya autenticado."""
        if requests is None:
            return (False, "Falta la librería 'requests'. Reinstala las dependencias.")
        if create_connection is None:
            return (False, "Falta 'websocket-client'. Ejecuta instalar.bat de nuevo.")
        if not self.token:
            return (False, "Falta el token de Deriv. Pégalo en Configuración.")
        if not self.token.lower().startswith("pat_"):
            self._log("[DERIV] Aviso: el token no empieza por 'pat_'. Deriv ahora usa "
                      "Personal Access Tokens (pat_...). Si falla, crea uno nuevo en "
                      "app.deriv.com/account/api-token con permiso Trade.")

        # 1) Listar cuentas del token
        url_acc = f"{REST_BASE}/trading/v1/options/accounts"
        self._log(f"[DERIV] GET {url_acc} (app_id={self.app_id}, token={self.token[:8]}...len={len(self.token)})")
        try:
            r = requests.get(url_acc, headers=self._headers(), timeout=20)
        except Exception as e:
            return (False, f"No pude contactar a Deriv (accounts): {e}")
        cuerpo = (r.text or "")[:400]
        self._log(f"[DERIV] accounts -> HTTP {r.status_code}: {cuerpo[:200]}")
        if r.status_code != 200:
            if r.status_code in (401, 403):
                return (False, f"Deriv {r.status_code} (token/permiso): {cuerpo}  "
                               "| Revisa que el token pat_ tenga el permiso 'Comercio/Trade', "
                               "que no haya vencido, y que el App ID sea válido.")
            return (False, f"Deriv respondió {r.status_code} al listar cuentas: {cuerpo}")
        try:
            cuentas = (r.json() or {}).get("data") or []
        except Exception as e:
            return (False, f"Respuesta de cuentas ilegible: {e}")
        if not cuentas:
            return (False, "El token no tiene cuentas de opciones asociadas.")

        # 2) Elegir la cuenta según el modo (demo/real) con blindaje
        deseado = "demo" if self._quiere_demo() else "real"
        elegida = None
        for c in cuentas:
            if str(c.get("account_type", "")).lower() == deseado:
                elegida = c
                break
        if elegida is None:
            tipos = ", ".join(sorted({str(c.get("account_type")) for c in cuentas}))
            if deseado == "demo":
                return (False, "No encontré una cuenta DEMO en tu token. Entra a Deriv, "
                               "activa/usa tu cuenta demo (virtual) y reintenta. "
                               f"(el token ve: {tipos})")
            return (False, "No encontré una cuenta REAL en tu token. "
                           f"(el token ve: {tipos})")

        self.account_id = elegida.get("account_id")
        self.account_type = str(elegida.get("account_type", "")).lower()
        self.is_virtual = (self.account_type == "demo")
        self.currency = elegida.get("currency", "USD") or "USD"
        try:
            self._balance = float(elegida.get("balance") or 0)
        except Exception:
            self._balance = 0.0
        self._log(f"[DERIV] Cuenta {deseado} elegida: {self.account_id} "
                  f"({self.currency}, saldo {self._balance}).")

        # 3) Pedir OTP -> URL de WebSocket ya autenticada
        try:
            ro = requests.post(
                f"{REST_BASE}/trading/v1/options/accounts/{self.account_id}/otp",
                headers=self._headers(), timeout=20)
        except Exception as e:
            return (False, f"No pude pedir el OTP a Deriv: {e}")
        if ro.status_code not in (200, 201):
            return (False, f"Deriv respondió {ro.status_code} al pedir OTP: {ro.text[:200]}")
        try:
            ws_url = (ro.json() or {}).get("data", {}).get("url")
        except Exception as e:
            return (False, f"Respuesta de OTP ilegible: {e}")
        if not ws_url:
            return (False, "Deriv no devolvió la URL de WebSocket (OTP).")

        # 4) Conectar al WebSocket (ya autenticado por el OTP en la URL)
        try:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
            self._ws = create_connection(ws_url, timeout=30)
        except Exception as e:
            return (False, f"No pude abrir el WebSocket de Deriv: {e}")

        self._log(f"[DERIV] WebSocket conectado (cuenta {self.account_type}).")
        return (True, "")

    def check_connect(self):
        try:
            if self._ws is None:
                return False
            r = self._enviar({"ping": 1}, timeout=10)
            return ("ping" in r) or (r.get("msg_type") == "ping")
        except Exception:
            return False

    def change_balance(self, modo):
        """Aplica el modo y BLINDA el demo: la cuenta se elige en connect()
        según el modo; aquí verificamos que coincida para no operar por
        error en la cuenta equivocada."""
        self.modo = modo
        if self.account_type is None:
            return  # aún no conectado; se valida en connect()
        quiere_demo = self._quiere_demo()
        if quiere_demo and self.account_type != "demo":
            raise RuntimeError(
                "Pediste modo PRÁCTICA pero la cuenta conectada es REAL. "
                "Reinicia el bot en modo Demo.")
        if (not quiere_demo) and self.account_type != "real":
            raise RuntimeError(
                "Pediste modo REAL pero la cuenta conectada es DEMO. "
                "Reinicia el bot en modo Real (y usa un token con cuenta real).")

    def get_balance(self):
        try:
            r = self._enviar({"balance": 1}, timeout=15)
            b = r.get("balance", {}) or {}
            self._balance = float(b.get("balance") or self._balance or 0)
        except Exception:
            pass
        return self._balance

    # ── velas (mismo formato de dict que usa el bot) ─────────
    def get_candles(self, par, timeframe, count, endtime=None):
        gran = int(timeframe) if timeframe else 60
        end = "latest"
        try:
            if endtime:
                end = int(endtime)
        except Exception:
            end = "latest"
        r = self._enviar({
            "ticks_history": par, "style": "candles", "granularity": gran,
            "count": int(count), "end": end, "adjust_start_time": 1,
        }, timeout=30)
        if "error" in r:
            raise RuntimeError(r["error"].get("message", "Deriv: error pidiendo velas"))
        velas = []
        for c in (r.get("candles") or []):
            try:
                ep = int(c["epoch"])
                velas.append({
                    "open":  float(c["open"]),
                    "close": float(c["close"]),
                    "min":   float(c["low"]),
                    "max":   float(c["high"]),
                    "from":  ep,
                    "to":    ep + gran,
                    "id":    ep,
                    "volume": 0,
                })
            except Exception:
                continue
        return velas

    # ── operar (mismo contrato que IQ_Option.buy) ────────────
    def buy(self, monto, par, direccion, expiracion):
        """Devuelve (ok, contract_id) o (False, motivo)."""
        ct = "CALL" if str(direccion).lower() in ("call", "buy", "up") else "PUT"
        try:
            prop = self._enviar({
                "proposal": 1, "amount": round(float(monto), 2), "basis": "stake",
                "contract_type": ct, "currency": self.currency,
                "duration": int(expiracion), "duration_unit": "m",
                # API NUEVA: el símbolo va en "underlying_symbol" (antes "symbol")
                "underlying_symbol": par,
            }, timeout=20)
        except Exception as e:
            return (False, f"proposal falló: {e}")
        if "error" in prop:
            return (False, prop["error"].get("message", "proposal rechazada"))
        p = prop.get("proposal", {}) or {}
        pid = p.get("id")
        ask = p.get("ask_price")
        if not pid:
            return (False, "Deriv no devolvió id de proposal")

        # ── GUARD DE PAYOUT ──────────────────────────────────
        try:
            pago_total = float(p.get("payout") or 0)
            costo = float(ask or 0)
            if costo > 0 and pago_total > 0:
                payout_pct = (pago_total - costo) / costo * 100.0
                self.ultimo_payout_pct = round(payout_pct, 1)
                self._log(f"[DERIV] {par} {ct}: payout del contrato {payout_pct:.1f}%")
                if self.payout_min > 0 and payout_pct < self.payout_min:
                    return (False, f"payout {payout_pct:.1f}% < mínimo configurado "
                                   f"{self.payout_min:.0f}% (deriv_payout_min) — entrada descartada")
        except Exception:
            pass

        try:
            b = self._enviar({"buy": pid, "price": ask}, timeout=20)
        except Exception as e:
            return (False, f"compra falló: {e}")
        if "error" in b:
            return (False, b["error"].get("message", "compra rechazada"))
        cid = (b.get("buy", {}) or {}).get("contract_id")
        if not cid:
            return (False, "Deriv no devolvió contract_id")
        return (True, cid)

    def check_win_v3(self, contract_id):
        """Espera a que el contrato cierre y devuelve la ganancia NETA
        (positiva si ganó, negativa si perdió). 'profit' viene como texto."""
        fin = time.time() + 60 * 60
        while time.time() < fin:
            try:
                r = self._enviar({"proposal_open_contract": 1, "contract_id": contract_id}, timeout=20)
            except Exception:
                time.sleep(2)
                continue
            poc = r.get("proposal_open_contract", {}) or {}
            if poc.get("is_sold"):
                try:
                    return float(poc.get("profit") or 0.0)
                except Exception:
                    return 0.0
            time.sleep(2)
        return 0.0

    check_win_v2 = check_win_v3

    # ── descubrimiento de activos (formas compatibles) ───────
    def get_all_ACTIVES_OPCODE(self):
        return {s: i + 1 for i, s in enumerate(self._simbolos)}

    def update_ACTIVES_OPCODE(self):
        return self.get_all_ACTIVES_OPCODE()

    def get_all_init_v2(self):
        actives = {}
        for i, s in enumerate(self._simbolos):
            actives[str(i + 1)] = {"name": s, "enabled": True, "is_suspended": False}
        return {"binary": {"actives": dict(actives)},
                "turbo":  {"actives": dict(actives)}}

    get_all_init = get_all_init_v2

    def get_all_open_time(self):
        d = {s: {"open": True} for s in self._simbolos}
        return {"turbo": dict(d), "binary": dict(d), "digital": dict(d)}

    def get_all_profit(self):
        return {}
