# ============================================================
#   conector_deriv.py — Conector para Deriv (índices sintéticos)
#
#   Expone LOS MISMOS métodos que el bot ya usa contra IQ Option
#   (connect, check_connect, change_balance, get_balance,
#   get_candles, buy, check_win_v3, get_all_*), pero por dentro
#   habla con la API WebSocket de Deriv. Así el resto del bot no
#   cambia: solo cambia QUIÉN es el cliente.
#
#   Deriv NO usa email/contraseña: usa un TOKEN de API que generas
#   en tu cuenta (Settings -> API token). Para PRACTICAR, genera el
#   token desde tu cuenta DEMO (virtual): este conector se NIEGA a
#   operar en real si pediste modo PRÁCTICA, y viceversa.
# ============================================================

import json
import time
import threading

try:
    from websocket import create_connection
except Exception:
    create_connection = None

# Índices sintéticos más comunes de Deriv. Puedes editar la lista desde
# config ("pares_deriv"). Estos están abiertos 24/7.
SIMBOLOS_DEFECTO = [
    "R_10", "R_25", "R_50", "R_75", "R_100",          # Volatility
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",  # Volatility (1s)
    "BOOM300N", "BOOM500", "BOOM1000",                 # Boom
    "CRASH300N", "CRASH500", "CRASH1000",              # Crash
    "STPRNG",                                          # Step Index
    "JD10", "JD25", "JD50", "JD75", "JD100",           # Jump
]


class ConectorDeriv:
    def __init__(self, token="", app_id="1089", modo="PRACTICE", simbolos=None,
                 payout_min=0.0, logger=None):
        self.token = (token or "").strip()
        self.app_id = str(app_id or "1089").strip()
        self.modo = modo or "PRACTICE"
        # Payout mínimo del contrato en % (0 = no bloquear, solo registrar).
        # En Boom/Crash ir a favor de la deriva paga poco; este guard evita
        # operar contratos cuyo retorno no compensa según tu configuración.
        self.payout_min = float(payout_min or 0)
        self._log = logger or (lambda *_a, **_k: None)
        self._simbolos = list(simbolos) if simbolos else list(SIMBOLOS_DEFECTO)
        self.ultimo_payout_pct = None   # payout % del último proposal (visible para el bot)
        self._ws = None
        self._lock = threading.Lock()     # serializa peticiones sobre el socket
        self._req_id = 0
        self.loginid = None
        self.is_virtual = None
        self.currency = "USD"
        self._balance = 0.0

    # ── infraestructura ──────────────────────────────────────
    def _url(self):
        return f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"

    def simbolos(self):
        return list(self._simbolos)

    def _enviar(self, payload, timeout=30):
        """Envía una petición y devuelve la respuesta con el mismo req_id.
        Descarta mensajes sueltos que no correspondan a esta petición."""
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

    # ── conexión (compatible con el flujo de IQ_Option) ──────
    def connect(self):
        """Devuelve (ok, motivo), igual que IQ_Option.connect()."""
        if create_connection is None:
            return (False, "Falta la librería 'websocket-client'. Ejecuta instalar.bat de nuevo.")
        if not self.token:
            return (False, "Falta el token de Deriv. Pégalo en Configuración.")
        try:
            # Si había un socket viejo (reconexión), lo cerramos.
            try:
                if self._ws is not None:
                    self._ws.close()
            except Exception:
                pass
            self._ws = create_connection(self._url(), timeout=30)
        except Exception as e:
            return (False, f"No pude abrir conexión con Deriv: {e}")
        try:
            r = self._enviar({"authorize": self.token}, timeout=30)
        except Exception as e:
            return (False, f"Error autorizando en Deriv: {e}")
        if "error" in r:
            return (False, r["error"].get("message", "token de Deriv inválido"))
        a = r.get("authorize", {}) or {}
        self.loginid = a.get("loginid")
        self.currency = a.get("currency", "USD") or "USD"
        self.is_virtual = bool(a.get("is_virtual"))
        try:
            self._balance = float(a.get("balance") or 0)
        except Exception:
            self._balance = 0.0
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
        """Aplica el modo y BLINDA el demo: si el token es de una cuenta que
        no coincide con el modo pedido, lanza error para NO operar por
        error en la cuenta equivocada."""
        self.modo = modo
        quiere_demo = str(modo).upper() in ("PRACTICE", "DEMO", "VIRTUAL")
        if self.is_virtual is None:
            return  # aún no autorizado; se validará tras connect()
        if quiere_demo and not self.is_virtual:
            raise RuntimeError(
                "El token de Deriv es de una cuenta REAL, pero pediste modo PRÁCTICA. "
                "Genera el token desde tu cuenta DEMO (virtual) para practicar sin riesgo.")
        if (not quiere_demo) and self.is_virtual:
            raise RuntimeError(
                "El token de Deriv es de una cuenta DEMO, pero pediste modo REAL. "
                "Genera un token desde tu cuenta real para operar en real.")

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
                "duration": int(expiracion), "duration_unit": "m", "symbol": par,
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
        # payout% = lo que GANAS neto si aciertas, relativo a lo apostado.
        # Ej: apuestas 10 y el contrato paga 19.50 -> payout 95%.
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
        (positiva si ganó, negativa si perdió), igual que IQ Option."""
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

    # También aceptamos check_win_v2 / check_win por si alguna ruta lo llama.
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

    # Alias por compatibilidad
    get_all_init = get_all_init_v2

    def get_all_open_time(self):
        # Los sintéticos operan 24/7: todos abiertos.
        d = {s: {"open": True} for s in self._simbolos}
        return {"turbo": dict(d), "binary": dict(d), "digital": dict(d)}

    def get_all_profit(self):
        # El payout de sintéticos varía por contrato; v1 lo deja vacío
        # (el bot cae a sus valores por defecto). Se puede calcular vía
        # proposal en una versión futura.
        return {}
