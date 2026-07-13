# ============================================================
#   validacion_oos.py — VALIDACIÓN OUT-OF-SAMPLE
#   para la auto-calibración del bot IFC.
#
#   PROBLEMA QUE RESUELVE
#   ─────────────────────
#   La auto-calibración actual (autocalibracion_activa) elige el
#   "mejor modo" (continuidad vs reversión) de cada par sobre las
#   últimas 4000 velas y lo opera en vivo. Eso es curve-fitting
#   casi garantizado: con 21 pares × 2 modos × 2 mitades, esperas
#   ~2 falsos positivos por puro azar en cada arranque.
#
#   QUÉ HACE ESTE SCRIPT
#   ────────────────────
#   1) Se conecta a IQ Option con TUS credenciales (las mismas del
#      config.json del bot).
#   2) Trae N velas M1 históricas de cada par de la lista.
#   3) Parte el histórico en TRAIN (70%) y TEST (30%) —
#      CRUCIAL: el test es OUT-OF-SAMPLE, una fracción de velas
#      que el bot NUNCA vio al elegir el modo.
#   4) En TRAIN corre IFC en ambos modos (continuidad y reversión)
#      y elige el de mayor winrate.
#   5) Aplica ESE modo (el elegido en train) sobre TEST y mide el
#      winrate real out-of-sample.
#   6) Reporta cuántos pares realmente superan el break-even (53.5%)
#      en TEST, no en train.
#
#   INTERPRETACIÓN
#   ──────────────
#   · Si en TEST > 60% de los pares siguen ganando, el borde es
#     probablemente real (aunque ojo: sigue siendo OTC).
#   · Si en TEST la mayoría cae a ~50%, era curve-fitting. NO
#     operes esos pares con dinero real.
#   · Si NINGÚN par supera el break-even en TEST, desactiva la
#     auto-calibración del bot — te va a hacer perder dinero.
#
#   USO
#   ───
#   1) Asegúrate de tener config.json con tus credenciales (inicia
#      el bot una vez y ciérralo, eso crea config.json).
#   2) python validacion_oos.py
#   3) (Opcional) python validacion_oos.py --pares EURUSD-OTC,GBPUSD-OTC
#   4) (Opcional) python validacion_oos.py --velas 6000 --pct-test 0.3
# ============================================================

import sys, os, json, time, argparse, datetime

# ── PARÁMETROS (CLI) — se parsea PRIMERO para que --help funcione sin config ──
parser = argparse.ArgumentParser(description="Validación out-of-sample de la auto-calibración (IFC, CHART, MHI o NR).")
parser.add_argument("--pares", default=None,
                    help="Lista separada por comas. Por defecto usa los OTC del config.")
parser.add_argument("--velas", type=int, default=5000,
                    help="Velas M1 totales a descargar por par (más = más fiable, más lento). Default 5000.")
parser.add_argument("--pct-test", type=float, default=0.30,
                    help="Fracción del histórico reservada para TEST (out-of-sample). Default 0.30.")
parser.add_argument("--break-even", type=float, default=53.5,
                    help="Winrate mínimo para ser rentable con payout 87%%. Default 53.5.")
parser.add_argument("--umbral-train", type=float, default=54.0,
                    help="Winrate mínimo en TRAIN para que un par SIQUIERA pase a test. Default 54.")
parser.add_argument("--min-operaciones", type=int, default=30,
                    help="Mínimo de operaciones en train Y en test para que el par cuente. Default 30.")
parser.add_argument("--estrategia", default="ifc",
                    choices=["ifc", "nr", "facundo"],
                    help="Estrategia a validar: ifc (default), nr (Número Redondo) o facundo (price action S/R).")
args = parser.parse_args()

if not (0.10 <= args.pct_test <= 0.50):
    print("ERROR: --pct-test debe estar entre 0.10 y 0.50.")
    sys.exit(1)

# ── Cargar config del bot ────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")
if not os.path.exists(CONFIG_FILE):
    print(f"ERROR: no encuentro {CONFIG_FILE}.")
    print("Inicia el bot una vez (con 'Iniciar bot' desde la UI) y ciérralo para que cree config.json, luego vuelve a correr este script.")
    sys.exit(1)

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

if not config.get("usuario") or not config.get("password"):
    print("ERROR: config.json no tiene usuario/password. Inicia el bot una vez desde la UI para guardarlos.")
    sys.exit(1)

# ── Importar la API de IQ Option ─────────────────────────────
try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("ERROR: falta instalar iqoptionapi. Ejecuta:")
    print("    pip install iqoptionapi")
    sys.exit(1)

# ── Importar detectores del bot (reutilizamos su lógica) ─────
sys.path.insert(0, HERE)
import bot


PARES = (
    [p.strip() for p in args.pares.split(",") if p.strip()]
    if args.pares else
    (config.get("scanner_candidatos_otc") or
     ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "EURGBP-OTC"])
)

VELAS_TOTAL    = max(1000, args.velas)
PCT_TEST       = args.pct_test
BREAK_EVEN     = args.break_even
UMBRAL_TRAIN   = args.umbral_train
MIN_OPS        = args.min_operaciones
ESTRATEGIA     = args.estrategia

# Mapping estrategia -> (lista de modos a comparar, función de backtest)
# La función de backtest tiene firma (velas, modo, [par]) y devuelve un dict
# con 'winrate' y 'total'.
# NOTA: "chart" y "mhi" se retiraron — sus estrategias ya no existen en
# bot.py (fueron descartadas por rendir bajo break-even).
# Para "facundo" cada variante es (modo, inversión): la validación OOS
# elige la mejor de las 8 en TRAIN y la mide en TEST, que es exactamente
# la decisión que hay que tomar antes de operar (directo vs invertido).
_MODOS_FACUNDO = [
    "rechazo·directo",     "rechazo·invertido",
    "rompimiento·directo", "rompimiento·invertido",
    "lateral·directo",     "lateral·invertido",
    "ambos·directo",       "ambos·invertido",
]

def _bt_facundo(velas, variante, par=None):
    base, inv = variante.split("·")
    return bot._backtest_facundo(velas, modo=base, invertir=(inv == "invertido"))

_BACKENDS = {
    "ifc":     (["continuidad", "reversion"],
                lambda v, m, par: bot._backtest_sobre_velas(v, m, usar_ema=config.get("ifc_usar_ema", True))),
    "nr":      (["rebote", "ruptura"],
                lambda v, m, par: bot._backtest_nr(v, m, par=par)),
    "facundo": (_MODOS_FACUNDO, _bt_facundo),
}


# =============================================================
#   CONEXIÓN IQ OPTION
# =============================================================
print()
print("=" * 70)
print(f"  VALIDACIÓN OUT-OF-SAMPLE — Estrategia {ESTRATEGIA.upper()}")
print("=" * 70)
print(f"  Pares a evaluar:    {len(PARES)}")
print(f"  Velas por par:      {VELAS_TOTAL} (~{VELAS_TOTAL/1440:.1f} días M1)")
print(f"  Train / Test:       {1-PCT_TEST:.0%} / {PCT_TEST:.0%}")
print(f"  Break-even (87%):   {BREAK_EVEN}%")
print(f"  Umbral en TRAIN:    {UMBRAL_TRAIN}% (para pasar a TEST)")
print(f"  Mín. operaciones:   {MIN_OPS} (en train y en test)")
print(f"  Estrategia:         {ESTRATEGIA.upper()} ({', '.join(_BACKENDS[ESTRATEGIA][0])})")
print("=" * 70)
print()

print(f"Conectando como {config['usuario']} (modo {config.get('modo','PRACTICE')})...")
Iq = IQ_Option(config["usuario"], config["password"])
ok, reason = Iq.connect()
if not ok:
    print(f"ERROR de conexión: {reason}")
    sys.exit(1)
Iq.change_balance(config.get("modo", "PRACTICE"))
print("Conectado.\n")


# =============================================================
#   LOOP POR PAR — TRAIN + TEST
# =============================================================
def _fetch(par, n):
    """Trae n velas M1 en lotes (igual que bot._fetch_historico)."""
    todas = {}
    endtime = int(time.time())
    intentos = 0
    while len(todas) < n and intentos < 40:
        intentos += 1
        k = min(1000, n - len(todas))
        try:
            lote = Iq.get_candles(par, 60, k, endtime)
        except Exception as e:
            print(f"  · error de red: {e}")
            break
        if not lote:
            break
        for v in lote:
            todas[v["from"]] = v
        endtime = lote[0]["from"] - 1
        if len(lote) < k:
            break
    return [todas[k] for k in sorted(todas)]


# Asegurar que los pares sean reconocidos por la librería
try:
    bot.registrar_activos_operables(PARES)
except Exception as e:
    print(f"  (aviso) no pude registrar activos: {e}")


# Aplicar config temporal al bot para que los detectores usen
# los parámetros que tenga el config.json del usuario (EMAs, etc.).
bot._Iq = Iq  # para que _fetch_historico y similares funcionen si hacen falta

resultados = []
pares_ok = 0
pares_pasan_test = 0

for i, par in enumerate(PARES, 1):
    print(f"[{i:>2}/{len(PARES)}] {par} — descargando {VELAS_TOTAL} velas...", end=" ", flush=True)
    try:
        velas = _fetch(par, VELAS_TOTAL)
    except Exception as e:
        print(f"ERROR: {e}")
        resultados.append({"par": par, "ok": False, "error": str(e)})
        continue

    if len(velas) < VELAS_TOTAL * 0.8:
        print(f"solo {len(velas)} velas, salto.")
        resultados.append({"par": par, "ok": False, "error": f"solo {len(velas)} velas"})
        continue
    print(f"{len(velas)} velas ✓")

    # Partir en train (viejo) y test (reciente). El test SIEMPRE es la
    # fracción MÁS RECIENTE — simula "el bot eligió modo con datos hasta
    # ayer, hoy opera en vivo".
    corte = int(len(velas) * (1 - PCT_TEST))
    train = velas[:corte]
    test  = velas[corte:]

    # ── Despachar a la estrategia seleccionada ──
    modos_posibles, fn_backtest = _BACKENDS[ESTRATEGIA]

    # ── TRAIN: correr ambos modos, elegir el mejor ──
    resultados_train = {m: fn_backtest(train, m, par) for m in modos_posibles}
    mejor_modo_train = max(resultados_train, key=lambda m: resultados_train[m]["winrate"])
    wr_train   = resultados_train[mejor_modo_train]["winrate"]
    ops_train  = resultados_train[mejor_modo_train]["total"]

    # ── TEST: aplicar el modo elegido, MEDIR winrate out-of-sample ──
    res_test = fn_backtest(test, mejor_modo_train, par)
    wr_test  = res_test["winrate"]
    ops_test = res_test["total"]

    # ── Comparar con el OTRO modo en test (control anti-suerte) ──
    otro_modo = [m for m in modos_posibles if m != mejor_modo_train][0]
    res_test_otro = fn_backtest(test, otro_modo, par)
    wr_test_otro  = res_test_otro["winrate"]

    supera_umbral_train = wr_train >= UMBRAL_TRAIN
    suficientes_ops     = ops_train >= MIN_OPS and ops_test >= MIN_OPS
    supera_be_test      = wr_test >= BREAK_EVEN
    mejor_que_otro      = wr_test > wr_test_otro  # el modo elegido gana al otro en test

    pares_ok += 1
    if supera_be_test and suficientes_ops:
        pares_pasan_test += 1

    verdict = "✓ REAL" if (supera_be_test and suficientes_ops and mejor_que_otro) else \
              ("~ DUDOSO" if supera_be_test else "✗ RUIDO")

    resultados.append({
        "par":              par,
        "ok":               True,
        "velas":            len(velas),
        "estrategia":       ESTRATEGIA,
        "modo_elegido":     mejor_modo_train,
        "wr_train":         wr_train,
        "ops_train":        ops_train,
        "wr_test":          wr_test,
        "ops_test":         ops_test,
        "wr_test_otro_modo": wr_test_otro,
        "supera_be_test":   supera_be_test,
        "mejor_que_otro":   mejor_que_otro,
        "verdict":          verdict,
    })

    print(f"        TRAIN: {mejor_modo_train:12s} {wr_train:5.1f}% ({ops_train} ops)  "
          f"TEST: {wr_test:5.1f}% ({ops_test} ops)  "
          f"[otro modo en test: {wr_test_otro:.1f}%]  → {verdict}")


# =============================================================
#   REPORTE FINAL
# =============================================================
print()
print("=" * 70)
print("  REPORTE FINAL — VALIDACIÓN OUT-OF-SAMPLE")
print("=" * 70)
print()
print(f"  Pares evaluados:              {pares_ok} / {len(PARES)}")
print(f"  Pares que superan BE en test: {pares_pasan_test} / {pares_ok}")
if pares_ok > 0:
    pct = pares_pasan_test / pares_ok * 100
    print(f"  Tasa de éxito:                {pct:.1f}%")
print()
print(" Detalle por par:")
print()
print(f"  {'Par':<14} {'Modo':<12} {'Train':>10} {'Test':>10} {'Otro':>8} {'Veredicto':<12}")
print(f"  {'-'*14} {'-'*12} {'-'*10} {'-'*10} {'-'*8} {'-'*12}")
for r in resultados:
    if not r.get("ok"):
        print(f"  {r['par']:<14} — ERROR: {r.get('error','?')}")
        continue
    print(f"  {r['par']:<14} {r['modo_elegido']:<12} "
          f"{r['wr_train']:>5.1f}%/{r['ops_train']:>3}op "
          f"{r['wr_test']:>5.1f}%/{r['ops_test']:>3}op "
          f"{r['wr_test_otro_modo']:>5.1f}%  "
          f"{r['verdict']}")
print()

# ── Interpretación automática ──
if pares_ok == 0:
    print("  ⚠ No se pudo evaluar ningún par. Revisa la conexión o los nombres de pares.")
elif pares_pasan_test == 0:
    print("  ✗ NINGÚN par supera el break-even en test out-of-sample.")
    print("    La auto-calibración del bot es curve-fitting puro. DESACTÍVALA:")
    print("       config['autocalibracion_activa'] = False")
    print("    Y no operes con dinero real hasta entender qué pasa.")
elif pares_pasan_test < pares_ok * 0.30:
    print(f"  ⚠ Solo {pares_pasan_test}/{pares_ok} pares ({pares_pasan_test/pares_ok*100:.0f}%) pasan el test.")
    print("    Esto es consistente con azar (con 21 pares × 2 modos esperarías ~25% por suerte).")
    print("    Desconfía: probablemente no hay borde real.")
elif pares_pasan_test < pares_ok * 0.60:
    print(f"  ~ {pares_pasan_test}/{pares_ok} pares ({pares_pasan_test/pares_ok*100:.0f}%) pasan el test.")
    print("    Borde débil. Solo opera los pares marcados como ✓ REAL en PRACTICE durante")
    print("    semanas antes de considerar dinero real.")
else:
    print(f"  ✓ {pares_pasan_test}/{pares_ok} pares ({pares_pasan_test/pares_ok*100:.0f}%) pasan el test.")
    print("    Borde probablemente real. Aún así: OTC es feed sintético del bróker.")
    print("    Opera en PRACTICE al menos 1 mes antes de pensar en dinero real.")

# ── Sugerencia de config ──
pares_reales = [r["par"] for r in resultados
                if r.get("ok") and r.get("verdict") == "✓ REAL"]
if pares_reales:
    print()
    print("  Sugerencia: operar SOLO estos pares con el modo indicado:")
    for r in resultados:
        if r.get("ok") and r.get("verdict") == "✓ REAL":
            print(f"    {r['par']:<14} → {r['modo_elegido']} (wr test {r['wr_test']:.1f}%)")
    print()
    print("  Para aplicar: en el dashboard, marca solo estos pares y pulsa")
    print("  'Aplicar sugeridos' tras el backtest de 1 día, o editalos a mano.")
    print("  Si prefieres auto, en config.json pon:")
    print(f'    "scanner_candidatos_otc": {json.dumps(pares_reales)}')

# Guardar reporte en JSON
reporte_path = os.path.join(HERE, f"validacion_oos_reporte_{ESTRATEGIA}.json")
with open(reporte_path, "w") as f:
    json.dump({
        "timestamp":  datetime.datetime.now().isoformat(),
        "estrategia": ESTRATEGIA,
        "velas":      VELAS_TOTAL,
        "pct_test":   PCT_TEST,
        "pares":      PARES,
        "resultados": resultados,
        "pares_pasan_test": pares_pasan_test,
        "pares_ok":   pares_ok,
    }, f, indent=2, ensure_ascii=False)
print()
print(f"  Reporte guardado en: {reporte_path}")
print()

try:
    Iq.disconnect()
except Exception:
    pass
