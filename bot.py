# ============================================================
#   BOT OTC M1 — IQ Option / Exnova
#
#   ESTRATEGIA: Timing exacto + EMAs rápidas + Filtros de calidad
#
#   INDICADORES:
#   · EMA 5, 9, 21  — señal rápida (cruce + stack)
#   · RSI 7         — filtro de agotamiento (zona 35-65)
#   · ADX 14        — filtro de consolidación (>= 25)
#   · Heiken Ashi   — confirmación visual (2 velas mismo color)
#   · Números redondos — filtro de zona peligrosa
#   · Soporte/Resistencia dinámico con 2+ toques
#
#   TIMING:
#   · Detecta señal en la vela actual
#   · Espera el inicio exacto de la siguiente vela (segundo 00)
#   · Entra en los primeros 3 segundos de la nueva vela
#
#   GESTIÓN:
#   · Máximo 3 entradas por sesión
#   · 2 pérdidas seguidas → pausa 30 minutos
#   · 3 pérdidas seguidas en el mismo par → descartar ese par
#     por un tiempo configurable (par_bloqueo_minutos)
# ============================================================

import time, json, os, threading, traceback, datetime, requests, math
import csv as _csv
import io as _io
try:
    from iqoptionapi.stable_api import IQ_Option
except Exception:
    IQ_Option = None   # permite usar el bot en modo Deriv sin iqoptionapi
from brokers import crear_conector, broker_actual

# =============================================================
# CONFIGURACIÓN
# =============================================================
config = {
    # ── BRÓKER ACTIVO ────────────────────────────────────────────────
    # "iq"    -> IQ Option (forex/OTC). Usa usuario + password.
    # "deriv" -> Deriv (índices sintéticos). Usa deriv_token (NO password).
    # Puedes cambiar de bróker cuando quieras desde el panel; cada uno
    # guarda sus propias credenciales y su propia lista de pares.
    "broker":               "iq",
    "usuario":              "",
    "password":             "",
    # Credenciales / ajustes de Deriv
    "deriv_token":          "",
    "deriv_app_id":         "1089",   # app_id público de pruebas de Deriv
    "pares_deriv":          ["R_75", "R_100", "R_50"],
    # ── ESTRATEGIA "SINTETICO" (para índices sintéticos de Deriv) ─────
    # Dos motores, elegidos AUTOMÁTICAMENTE según el índice:
    #  · BOOM*/CRASH* -> SPIKE-RIDE: opera a favor de la deriva estructural
    #    (Crash sube entre caídas -> CALL; Boom baja entre subidas -> PUT),
    #    evitando entrar justo después de un spike.
    #  · R_*/1HZ* (Volatility) -> REVERSIÓN Z-SCORE: entra contra extremos
    #    estadísticos (z-score alto + racha de velas del mismo color).
    "sintetico_spike_factor":     5.0,  # rango > factor×mediana = spike
    "sintetico_post_spike_velas": 3,    # velas de espera tras un spike
    "sintetico_z_periodo":        20,   # ventana del z-score (Volatility)
    "sintetico_z_umbral":         1.8,  # |z| mínimo para señal
    "sintetico_racha_min":        3,    # velas seguidas del mismo color
    # Payout mínimo del contrato en Deriv (0 = solo registrar en log, no
    # bloquear). Si >0, el bot NO entra cuando el payout del contrato es
    # menor — clave en Boom/Crash, donde ir a favor de la deriva paga poco.
    "deriv_payout_min":           0,
    "modo":                 "PRACTICE",   # "PRACTICE" o "REAL"
    "pares":                ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "EURGBP-OTC"],
    # ── Mercado normal (forex real, sin "-OTC") ───────────────────────
    # A diferencia de OTC (simulado, disponible 24/7), estos pares solo
    # se pueden operar cuando el mercado real está abierto (de domingo
    # noche a viernes noche, según horario de cada par). Si está activo,
    # el bot agrega a la rotación SOLO los pares de esta lista que la
    # API de IQ Option reporte como abiertos en ese momento — nunca
    # intenta forzar uno que esté cerrado.
    "pares_normales_activo":  False,
    "pares_normales":         ["EURUSD", "GBPUSD", "USDJPY", "EURGBP"],
    # ── Escáner de pares al iniciar ─────────────────────────────────
    # Al dar "Iniciar bot": primero se escanean TODOS los candidatos
    # (lista amplia de OTC + los pares normales abiertos, si esa opción
    # está activa), se puntúa la calidad de tendencia de cada uno (0-100)
    # y se opera SOLO con los scanner_top_n mejores. Con scanner_activo
    # en False, el bot usa los pares marcados a mano, como siempre.
    "scanner_activo":  True,
    "scanner_top_n":   4,
    "scanner_velas":   100,   # velas M1 que se piden por par para puntuarlo
    "scanner_candidatos_otc": [
        "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "EURGBP-OTC",
        "EURJPY-OTC", "GBPJPY-OTC", "AUDUSD-OTC", "USDCAD-OTC",
        "USDCHF-OTC", "NZDUSD-OTC", "AUDJPY-OTC", "AUDCAD-OTC",
        "AUDCHF-OTC", "CADJPY-OTC", "CHFJPY-OTC", "EURCAD-OTC",
        "EURCHF-OTC", "EURAUD-OTC", "GBPAUD-OTC", "GBPCAD-OTC",
        "GBPCHF-OTC",
    ],
    # ── Rotación automática de pares ─────────────────────────────────
    # Cada scanner_rotacion_min minutos se re-escanean los candidatos.
    # Si un par operando quedó LATERAL (sin tendencia), se apaga (su hilo
    # termina la operación en curso y se detiene solo) y entra en caliente
    # el mejor candidato con tendencia clara y score >= rotacion_score_min.
    "scanner_rotacion_activa":    True,
    "scanner_rotacion_min":       30,
    "scanner_rotacion_score_min": 55,
    # ── AUTO-CALIBRACIÓN por par (borde MEDIDO, no tendencia) ─────────
    # Al iniciar, el bot corre el backtest IFC (ambos modos) sobre las
    # últimas autocal_velas de CADA par candidato, con datos reales del
    # bróker. Se queda con los autocal_max_pares mejores cuyo mejor modo
    # supere autocal_umbral, y opera CADA PAR CON SU PROPIO MODO (p.ej.
    # GBPCAD en continuidad y GBPCHF en reversión a la vez). Si ningún
    # par supera el umbral, cae al escáner de tendencia como respaldo.
    "autocalibracion_activa": True,
    "autocal_velas":     4000,
    "autocal_umbral":    54.0,
    "autocal_max_pares": 4,
    # Validación DOBLE (anti "ganador con suerte"): el histórico de cada
    # par se parte en dos mitades y el par solo se selecciona si su mejor
    # modo supera autocal_umbral_mitad en AMBAS. Un borde real persiste en
    # las dos; un golpe de suerte reciente, no.
    "autocal_validacion_doble": True,
    "autocal_umbral_mitad":     52.0,
    # ── LISTA NEGRA en vivo ────────────────────────────────────────────
    # Si un par acumula en la sesión lista_negra_min_ops operaciones o
    # más con winrate <= lista_negra_max_wr, se banquea por el resto de
    # la sesión (su hilo termina la operación en curso y se apaga solo).
    "lista_negra_activa":  True,
    "lista_negra_min_ops": 8,
    "lista_negra_max_wr":  40.0,
    # ── SUSTITUCIÓN POR RACHA ──────────────────────────────────────────
    # Cuando un par acumula par_perdidas_max pérdidas SEGUIDAS (el mismo
    # umbral del bloqueo por par), se apaga con seguridad (termina su
    # operación en curso) y entra EN CALIENTE el siguiente mejor par
    # disponible de la tabla de calibración (con su modo propio), o del
    # ranking del escáner si no hubo calibración. El bloqueado no vuelve
    # en esta sesión.
    "racha_sustitucion_activa": True,
    # ── MODO DE OPERACIÓN ─────────────────────────────────────────────
    # "manual" (por defecto): opera EXACTAMENTE los pares que el usuario
    #   marca en la interfaz, cada uno con su modo IFC guardado por par
    #   (config["ifc_modo_por_par"]). Se ignora el escáner y la auto-
    #   calibración automáticos al iniciar — el usuario decide qué se
    #   opera y con qué polaridad usando el botón "Detectar pares" y el
    #   backtest de 1 día por par. Marcar/desmarcar un par o cambiarle el
    #   modo desde la UI se aplica EN CALIENTE (sin reiniciar).
    # "auto": comportamiento anterior — al iniciar corre autocal + scanner,
    #   la rotación automática decide qué opera.
    "modo_operacion": "manual",
    # Modo IFC por par (persistente, editado por el usuario). Tiene la
    # prioridad más alta al decidir la polaridad de un par.
    # Formato: {"USDCHF-OTC": "continuidad", "EURCHF-OTC": "reversion", ...}
    "ifc_modo_por_par": {},
    # Persistencia de sesión del día en disco (sesion.json). Si se
    # detiene y se vuelve a iniciar el bot el MISMO día, se recuperan
    # wins, losses, neto, racha, operaciones y bloqueos de pares.
    # Al cambiar la fecha se resetea automáticamente. Existe además un
    # botón manual "Reiniciar día" en la UI.
    "sesion_persistente": True,
    # ── Timing de entrada ────────────────────────────────────────────
    # Segundo de la vela en que cada par pide velas y analiza. Antes era 57
    # (vela de fuerza ~95% formada), pero eso dejaba solo 3 segundos para
    # get_candles + análisis + espera al 00 — poco si la red está lenta.
    # TIMING-FIX: bajado a 55 para dejar 5 segundos de margen. La vela de
    # fuerza aún está ~92% formada al :55, suficiente para detectarla.
    # La entrada normal es al segundo 00 EXACTO; si el análisis termina
    # apenas pasado el 00, se permite entrar hasta entrada_tolerancia_seg.
    "analisis_segundo": 55,
    "entrada_tolerancia_seg": 2,
    # ── Selector de estrategia ───────────────────────────────────────
    # "estrategia": cuál opera EN VIVO: "ifc" (indecisión-fuerza),
    # cuadrantes de 5 minutos).
    "estrategia": "ifc",
    #   "rebote":  la vela toca un nivel probado y cierra devuelta con
    #              mecha de rechazo -> operar el rebote (PUT en resistencia,
    #              CALL en soporte).
    #   "ruptura": la vela cierra MÁS ALLÁ del nivel con cuerpo decidido ->
    #              operar la continuación de la ruptura.
    # ── Estrategia IFC-PRO (versión del trader: fuerza rompe nivel +
    #    entrada en el RETROCESO a la zona de activación) ──────────────
    # Secuencia real que enseña el trader:
    #   1) indecisión (doji) tras un movimiento -> su mecha marca un nivel
    #   2) FUERZA que ROMPE ese nivel (cierra más allá) -> el nivel roto se
    #      vuelve "zona de activación" (polaridad)
    #   3) NO se entra de una: se espera a que el precio RETROCEDA y toque
    #      esa zona; ahí se entra a favor de la fuerza. La entrada real
    #      ocurre en la apertura de la vela siguiente al toque.
    "ifcpro_pullback_atr":   0.15,  # cerca de la zona = a <= 0.15*ATR del nivel
    "ifcpro_espera_max":     8,     # velas máx. esperando el retroceso; si no, se descarta
    "ifcpro_ruptura_min_atr":0.05,  # la fuerza debe cerrar más allá del nivel al menos 0.05*ATR
    "ifcpro_usar_ema":       True,  # exigir que la fuerza vaya a favor de la tendencia EMA
    # Cuadrantes alineados al reloj (:00-:04, :05-:09, ...). Se cuentan
    # los colores de las 3 velas de la ventana y se entra en la vela
    # siguiente apostando al color MINORITARIO (clásica) o al mayoritario.
    # Si alguna de las 3 es doji, ese ciclo se salta (regla clásica).
    # ── Estrategia NÚMERO REDONDO ("El Hermoso") ──────────────────────
    # Zonas institucionales: niveles de precio "redondos" (cada 50/100
    # pips en forex, cada 0.50 en JPY) donde el trader del PDF reporta
    # que se acumulan órdenes limitadas bancarias. Funciona como
    # soporte/resistencia con reglas precisas:
    #   1) POLARIDAD: si precio > nivel, SOLO COMPRA (rebote desde abajo).
    #      Si precio < nivel, SOLO VENTA (rebote desde arriba).
    #   2) RUPTURA: una vela que cierra con cuerpo >= nr_ruptura_cuerpo_min
    #      Y al menos nr_ruptura_margen_pips pips más allá del nivel
    #      INVIETE la polaridad (lo que era soporte se vuelve resistencia).
    #   3) MÁXIMO 3 TOQUES por nivel: tras el 3er toque, no se opera más
    #      ese nivel hasta que cambie la polaridad (ruptura).
    #   4) RELLENO DE MECHAS: el punto real de entrada se DESPLAZA según
    #      la última mecha que cruzó el nivel. Si la mecha inferior cruzó
    #      el nivel 5 pips y rebotó, el nivel real está 5 pips más abajo.
    #   5) Excelente punto de entrada: mira la MECHE más cercana al nivel
    #      en las últimas velas; esa es tu referencia real, no el abstracto.
    #
    # Dos modos comparables a IFC:
    #   "rebote"     = operar el rebote en el nivel (polaridad vigente)
    #   "ruptura"    = operar la continuación tras ruptura confirmada
    "nr_tolerancia_pips":         5,     # pips: distancia máxima al nivel para contar "toque"
    "nr_max_toques":              3,     # tras el 3er toque, no se opera más ese nivel
    "nr_ruptura_cuerpo_min":      0.60,  # cuerpo mínimo de la vela de ruptura (fracción del rango)
    "nr_ruptura_margen_pips":     3,     # cierre más allá del nivel por al menos N pips
    "nr_niveles_100pips":         True,  # considerar niveles cada 100 pips (más fuertes)
    "nr_niveles_50pips":          True,  # considerar niveles cada 50 pips (intermedios)
    "nr_niveles_10pips":          False, # niveles cada 10 pips (mucho ruido en M1, off por defecto)
    "nr_ventana_toques":          15,    # velas hacia atrás para contar toques de un nivel
    "nr_distancia_min_toque_pips": 8,    # pips: el precio debe venir de al menos esta distancia para contar como toque
                                         # (evita contar velas que solo "pasan por" el nivel)
    "nr_relleno_mechas":          True,  # activar el desplazamiento por mechas (regla 4)
    "nr_usar_ema":                False, # filtro EMA opcional (coincidir con tendencia)
    "nr_modo":                    "rebote",  # "rebote" o "ruptura" (para backtest comparativo)
    "timeframe":            60,
    "velas_analisis":       250,
    # AJUSTE v9.1.1: este campo "expiracion" es ZOMBIE — el bot ignora este valor
    # y calcula la expiración real como `timeframe // 60` en loop_par (línea ~3994).
    # Se mantiene por compatibilidad con config.json existentes, pero cambiarlo
    # aquí NO tiene efecto en la operativa. Para cambiar la expiración real,
    # modificar `timeframe` arriba (60s = 1 min, 120s = 2 min, etc.).
    "expiracion":           1,            # minutos (NO USADO — ver comentario arriba)
    "monto":                1.0,          # USD por operación (base; se ajusta si position_sizing_activa)
    # ── POSITION SIZING (riesgo dinámico) ────────────────────────────
    # Si está activo, el monto base NO es config['monto'] fijo sino:
    #   monto = clamp(balance * riesgo_pct% * fracción_kelly, monto_min, monto_max)
    # donde fracción_kelly = max(0, (p * payout - (1-p)) / payout) * kelly_fraction
    # y p = winrate calibrado del par (si existe), o 0.52 por defecto
    # (ligero optimismo sobre el azar para no apuntar a 0). Kelly
    # fraccional (1/4 del óptimo) es el estándar conservador — evita
    # la varianza brutal del Kelly completo.
    # IMPORTANTE: por defecto DESACTIVADO para no cambiar el comportamiento
    # existente. Actívalo con True si quieres gestión de riesgo real.
    "position_sizing_activa":  False,
    "position_sizing_riesgo_pct":   2.0,   # % del balance por operación
    "position_sizing_min":          1.0,   # monto mínimo (IQ Option no acepta menos)
    "position_sizing_max":          50.0,  # monto máximo por operación
    "position_sizing_kelly_frac":   0.25,  # 1/4 de Kelly (conservador)
    "position_sizing_default_p":    0.52,  # winrate por defecto si no hay calibrado
    "position_sizing_payout":       0.87,  # payout para el cálculo de Kelly
    # ── LÍMITE DE EXPOSICIÓN TOTAL ───────────────────────────────────
    # Aunque varios pares estén operando en paralelo, no comprometer más
    # de exposición_max_pct% del balance en operaciones simultáneas
    # (suma de montos abiertos). Si se alcanza el límite, las nuevas
    # señales se descartan con log "[EXPOSICION] límite alcanzado".
    "exposicion_max_pct":    10.0,         # % del balance, máximo en vuelo
    "martingala_activa":    False,        # activar/desactivar martingala
    "martingala_mult":      2.0,          # multiplicador por pérdida (ej: 2.0 = doble)
    "martingala_niveles":   1,            # niveles antes de volver al monto base
    # ── LÍMITE DE PÉRDIDA TOTAL DE LA MARTINGALA ─────────────────────
    # Aunque el usuario ponga martingala_niveles=3, NO se permite que la
    # racha completa de martingala supere martingala_max_exposicion_usd
    # dólares (suma de montos base + reentradas). Pensado para evitar
    # la ruina acelerada clásica de martingalas de 3 niveles con montos
    # grandes: 1+2+4=7 ya es el 700% del monto base en una sola racha.
    # Si la siguiente entrada excede este límite, se resetea a monto base.
    "martingala_max_exposicion_usd": 10.0,
    # Indicadores
    "ema_mid":              9,
    "ema_slow":             21,
    "rsi_period":           7,
    "adx_period":           14,
    "adx_minimo":           25,
    "rsi_upper":            65,
    "rsi_lower":            35,
    # Histéresis de tendencia: qué tan lejos de la SMA34 (en fracción
    # del rango promedio de vela) debe moverse el precio para que la
    # tendencia cambie de ALCISTA a BAJISTA o viceversa. Subir este
    # valor = tendencia más "pegajosa" (menos señales, más filtradas).
    # Bajarlo = vuelve casi al comportamiento original (más sensible).
    "tendencia_buffer_factor": 0.3,
    # ── Entrada por tendencia sostenida (independiente del cruce MACD) ──
    # Si la tendencia SMA34 se mantiene N velas consecutivas sin cambiar,
    # el bot puede entrar a favor de esa tendencia aunque el MACD no haya
    # cruzado. Pensado para aprovechar tendencias largas y "planas" donde
    # el MACD (1/34/5, muy lento) se queda sin cruzar durante mucho rato.
    "tendencia_sostenida_activa":  False,  # True para activar esta vía
    "tendencia_sostenida_velas":   5,      # velas seguidas con misma tendencia para confiar en ella
    # ── Filtro ADX: fuerza de tendencia (evita operar en mercado lateral) ──
    # No indica dirección (eso ya lo dan MACD y SMA34): indica si la
    # tendencia detectada tiene fuerza real o es solo ruido lateral.
    # Si ADX < adx_minimo, la señal se descarta aunque MACD+SMA34 hayan
    # coincidido — es la situación más común de "falsa señal".
    "adx_filtro_activo":  False,   # True para activar este filtro
    "adx_periodo":        14,
    "adx_minimo":          20,     # por debajo de esto se considera lateral/sin tendencia
    # ── Detección de vela institucional/anómala ───────────────────────
    # Si una vela cierra con un rango (max-min) mucho mayor al promedio
    # reciente (típico de noticias fuertes o movimientos abruptos en
    # OTC), se bloquea el par por 'par_bloqueo_minutos' minutos para
    # dejar que el mercado se estabilice antes de volver a operar.
    # Reutiliza el mismo sistema de bloqueo que ya existe para pérdidas.
    "vela_institucional_activa":        True,   # AJUSTE v9.1.1: activado para proteger contra velas anómalas (noticias/apertura de sesión). No filtra por par ni por hora, solo bloquea velas con rango > 3.5x el promedio.
    "vela_institucional_periodo":       20,    # velas usadas para calcular el rango promedio
    "vela_institucional_multiplicador": 3.5,   # cuántas veces el promedio para considerarla anómala
    # ── Estrategia: INDECISIÓN, FUERZA y CONTINUIDAD (IFC) ────────────
    # 3 velas: (1) indecisión = vela con mecha arriba y abajo y cuerpo
    # pequeño; (2) fuerza = vela decidida (cuerpo >= 70% del rango) que
    # cubre la vela de indecisión; (3) continuidad = se entra en la
    # apertura de la vela siguiente, en la dirección de la vela de fuerza.
    # Filtro de tendencia por EMAs: solo compras si es alcista, ventas si
    # es bajista (nunca contra la tendencia).
    "ifc_indecision_cuerpo_max": 0.50,   # cuerpo <= 50% del rango  -> indecisión (no domina)
    "ifc_indecision_rango_max_x": 2.0,   # "que no sea tan grande": rango <= 2x el promedio
    "ifc_fuerza_cuerpo_min":     0.60,   # cuerpo >= 60% del rango  -> fuerza
    "ifc_fuerza_cuerpo_max":     0.85,   # cuerpo <= 85% del rango  -> fuerza (tope: vela "demasiado perfecta" casi sin mechas se descarta)
    "ifc_mismo_color":           False,  # exigir indecisión y fuerza del mismo color (más estricto)
    "ifc_usar_ema":              True,   # filtro de tendencia por EMAs
    "ifc_ema_rapida":            9,
    "ifc_ema_lenta":             21,
    # Dirección de la entrada: "continuidad" = a favor de la vela de fuerza
    # (lo que enseña el trader); "reversion" = apuesta lo CONTRARIO a la
    # fuerza. En datos reales de forex la reversión dio mejor tasa; pruébalo
    # en TU OTC con el backtest incluido antes de decidir.
    "ifc_modo":                  "continuidad",
    # ── Estrategia FACUNDO (price action sobre líneas importantes) ────
    # Basada en el video "Táctica 99% EFECTIVA para GANAR en OTC" de
    # Facundo Contreras. Estrategia de price action puro sobre S/R:
    # trazar soportes/resistencias/máximos/mínimos y operar cuando el
    # precio se acerca + deja mecha de rechazo O rompe con cuerpo + la
    # siguiente vela confirma. Tres variantes seleccionables:
    #   "rechazo"     = vela deja mecha en S/R + siguiente confirma
    #   "rompimiento" = vela rompe S/R con cuerpo + siguiente confirma
    #   "lateral"     = mercado en rango, entrar en extremos
    #   "ambos"       = rechazo + rompimiento (lateral se invoca aparte
    #                   en backtest; en vivo no se mezcla con laterales)
    "facundo_modo":                 "ambos",
    "facundo_sr_lookback":          80,       # velas atrás para detectar S/R (ahora sí se respeta)
    "facundo_sr_toques_previos":    2,        # toques PREVIOS del nivel (velas distintas) antes de entrar.
                                              # 2 previos + el toque de la entrada = ENTRADA EN EL 3er TOQUE.
                                              # AJUSTE v9.1.2: este es el MÍNIMO. Si en la misma vela hay
                                              # otro nivel con 3+ toques previos (entrada en 4to/5to), el
                                              # bot ahora lo PRIORIZA automáticamente (ver detectar_facundo_*
                                              # y _prioridad_senal con bono +8 por toque extra).
    "facundo_sr_toques_max_previos": 0,       # 0 = SIN TOPE. Permite operar 4to, 5to, 6to toque (los
                                              # niveles más probados son más confiables). Si quieres
                                              # limitar a solo 3er/4to toque, poner 3 aquí.
                                              # Validado el 11/7 con 0: 56.9% en 51 ops.
    "facundo_sr_separacion_velas":  3,        # velas mínimas entre toques del mismo nivel (3 = config
                                              # validada). OJO: subir esto también cambia la población de
                                              # señales (medido: sep 5 con tope 0 → 220 vs 240 señales).
    "facundo_sr_zone_pips":         0.0003,   # tolerancia "precio cerca de nivel"
    "facundo_mecha_min_ratio":      0.30,     # mecha >= 30% del rango = rechazo
    "facundo_confirm_cuerpo_min":   0.40,     # cuerpo vela confirmación >= 40%
    "facundo_romp_cuerpo_min":      0.60,     # cuerpo vela rompimiento >= 60%
    "facundo_romp_margen_pips":     0.0003,   # cierre más allá del nivel por N pips
    "facundo_lateral_lookback":     30,       # velas para identificar el rango
    "facundo_lateral_tolerancia_pips": 0.0005, # tolerancia "toca el extremo"
    "facundo_lateral_max_amplitud_x": 8,      # máx amplitud del rango vs vela promedio
    "facundo_usar_ema":             True,     # filtro de tendencia sobre la dirección FINAL de entrada
    "facundo_payout_min":           0,        # 0 = DESACTIVADO: opera con cualquier payout (elección del
                                              # usuario: su bróker no paga 90%). Si algún día quieres el
                                              # filtro del video, pon aquí el % mínimo (ej. 87 o 90).
                                              # Ojo: payout más bajo = break-even más alto (87% → 53.5%,
                                              # 80% → 55.6%, 70% → 58.8%).
    # ── Inversión de dirección (anti-curve-fitting) ─────────────────
    # Si tras operar la estrategia original en vivo se observa que las
    # entradas van "al revés" (racha de pérdidas consistente), activar
    # este flag invierte la dirección de TODAS las señales Facundo:
    #   - rechazo en resistencia: era PUT  → ahora CALL
    #   - rechazo en soporte:     era CALL → ahora PUT
    #   - rompimiento alcista:    era CALL → ahora PUT  (apuesta a falso)
    #   - rompimiento bajista:    era PUT  → ahora CALL (apuesta a falso)
    #   - toca techo del rango:   era PUT  → ahora CALL
    #   - toca piso del rango:    era CALL → ahora PUT
    # Los detectores NO cambian: siguen identificando S/R, mechas y
    # rompimientos igual. Solo se invierte la dirección de la entrada.
    # El backtest respeta el flag, así se puede comparar invertido vs
    # directo sobre el histórico antes de operar en real.
    "facundo_invertir":             True,
    # ── Micro-tendencia dominante por fuerza de velas ──────────────────
    # Mira las últimas 'micro_tendencia_velas' velas y mide qué dirección
    # domina, contando solo velas "fuertes" (cuerpo grande respecto a su
    # rango, poca mecha = movimiento decidido, no indecisión). Si una
    # dirección domina claramente, bloquea cualquier señal que vaya en
    # contra — pensado para evitar comprar/vender en contra de un
    # movimiento reciente claro, aunque la tendencia SMA34 diga lo
    # contrario (rebote de corto plazo dentro de otra tendencia).
    #
    # IMPORTANTE sobre la ventana: SMA34 (tu indicador de tendencia
    # principal) ya mira 34 velas. Si esta ventana es muy parecida a esa
    # (ej. 30), el filtro casi nunca va a discrepar con la SMA34 y se
    # vuelve redundante — por diseño matemático, no por error. Por eso
    # el valor por defecto es más corto (15): así captura algo más
    # rápido y reciente que la SMA34, que es justo el tipo de rebote
    # de corto plazo que se quiere detectar.
    #
    # IMPORTANTE sobre calibración: estos umbrales (fuerza mínima y
    # % de dominancia) dependen del "carácter" real del ruido de cada
    # par/horario, y no hay forma de calibrarlos bien sin observar el
    # bot en vivo. Si lo activas y nunca ves ninguna señal bloqueada
    # por "micro-tendencia" en el log, bájalos un poco (ej. fuerza 0.5
    # -> 0.35, dominancia 0.6 -> 0.45) hasta que empiece a actuar.
    "micro_tendencia_activa":      False,
    "micro_tendencia_velas":       15,    # tamaño de la ventana a evaluar
    "micro_tendencia_fuerza_min":  0.5,   # cuerpo/rango mínimo para que una vela cuente como "fuerte"
    "micro_tendencia_dominancia":  0.6,   # % de velas fuertes en una dirección para considerarla dominante
    "token_telegram":       "",
    "chat_id":              "",
    "max_perdidas_seguidas": 3,
    "max_perdidas_dia":      5,
    "objetivo_dia":          10,
    "sr_lookback":          50,
    "sr_touch_min":         2,
    "sr_zone_pips":         0.0003,
    "round_zone":           0.0005,
    # Filtro de bloqueo por par: si un par pierde N seguidas, se
    # descarta por "par_bloqueo_minutos" minutos.
    "par_perdidas_max":     3,            # pérdidas seguidas para bloquear el par
    "par_bloqueo_minutos":  60,           # minutos de bloqueo del par
    # Warm-up al arranque
    "warmup_velas":              30,      # velas históricas a simular
    "warmup_pausa_segundos":    120,      # segundos de pausa (120 = 2 min)
}

# ── Carpeta de DATOS (separada del código) ───────────────────
# config.json / sesion.json / historial viven en una carpeta escribible
# aparte del código. Así el auto-actualizador puede reemplazar los .py y el
# .html SIN borrar tu configuración, tu sesión ni tu historial.
def _dir_datos():
    base = os.environ.get("BOT_IFC_DATOS")
    if not base:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    return base

DATA_DIR = _dir_datos()

# Migración suave: si venías de una versión que guardaba estos archivos junto
# al código (en la carpeta raíz), los movemos una sola vez a datos/.
def _migrar_datos_legacy(nombre):
    try:
        viejo = os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre)
        nuevo = os.path.join(DATA_DIR, nombre)
        if os.path.exists(viejo) and not os.path.exists(nuevo) and os.path.abspath(viejo) != os.path.abspath(nuevo):
            os.replace(viejo, nuevo)
    except Exception:
        pass

for _n in ("config.json", "sesion.json", "historial_operaciones.csv"):
    _migrar_datos_legacy(_n)

CONFIG_FILE  = os.path.join(DATA_DIR, "config.json")
SESION_FILE  = os.path.join(DATA_DIR, "sesion.json")
# Historial PERMANENTE de operaciones (no se resetea al cambiar de día,
# a diferencia de sesion.json). Es el registro externo que el usuario
# conserva y abre en Excel; también la fuente del Excel de estadísticas.
HISTORIAL_CSV = os.path.join(DATA_DIR, "historial_operaciones.csv")
_historial_lock = threading.Lock()
_HISTORIAL_COLUMNAS = [
    "timestamp", "fecha", "hora", "dia_semana", "franja_horaria",
    "par", "estrategia", "direccion", "resultado",
    "monto", "ganancia", "balance", "racha_perdidas_momento",
]
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# =============================================================
# ESTADO GLOBAL (usado por servidor.py / app.py)
# =============================================================
estado = {
    "corriendo":         False,
    "conectado":         False,
    "modo":              "PRACTICE",
    "balance":           0.0,
    "wins":              0,
    "losses":            0,
    "neto":              0.0,
    "perdidas_seguidas": 0,
    "operaciones":       [],
    "log":               [],
    "pares_estado":      {},
    "error":             "",
    "martingala_nivel":  0,       # nivel actual de martingala (0 = monto base)
    "martingala_monto":  0.0,     # monto actual incluyendo martingala
    "pares_bloqueados":  {},      # {par: timestamp_desbloqueo}
    "warmup_resultados": {},      # {par: {wins, losses, tasa, tendencia}} — resultado del testeo previo
    "warmup_ranking_enviado": False,
    "pares_operando":    [],      # lista efectiva de pares en esta corrida (OTC + normales abiertos)
    "exposicion_en_vuelo": 0.0,   # suma de montos de operaciones actualmente abiertas
    "watchdog_atascados":  [],    # lista de pares marcados como atascados por el watchdog
}

_hilo_bot = None
_Iq       = None
_lock     = threading.Lock()
_buy_lock = threading.Lock()   # serializa las llamadas a _Iq.buy() y get_candles()
_conn_lock = threading.Lock()  # serializa TODA reconexión (evita 2 connect() a la vez)
_ultima_reconexion_forzada = 0.0   # timestamp de la última reconexión forzada (anti-tormenta)

# Identificador de "generación" de ejecución. Cada vez que se llama a
# iniciar_bot() se incrementa. loop_bot() y loop_par() guardan el valor
# vigente al arrancar y lo comparan en cada vuelta: si ya no coincide
# (porque se detuvo y se volvió a iniciar el bot), el hilo se cierra solo
# en vez de seguir vivo usando un _Iq que ya no le corresponde.
_run_id   = 0
# Pares "apagados" por la rotación automática: el hilo del par termina su
# operación en curso (nunca se corta a mitad de una compra) y se detiene
# solo al ver su nombre aquí. Se limpia en cada inicio del bot.
_pares_desactivados = set()
# ── Coordinador de entradas por vela ──────────────────────────────────
# Si 2+ pares señalan en la MISMA vela, solo entra el de mayor prioridad
# (cuerpo de fuerza + mismo color + ADX). Los demás ceden esa vela. Así
# nunca hay dos compras compitiendo por el lock al segundo 00.
_entrada_lock = threading.Lock()
_entradas_por_minuto = {}   # minuto -> {"candidatos": {par: prioridad}, "ganador": str|None}
# ── Watchdog de hilos por par ─────────────────────────────────────────
# Cada loop_par anota aquí el timestamp (epoch) de su última iteración
# completada. Un hilo watchdog revisa cada 60s: si un par lleva más de
# WATCHDOG_PAR_STUCK_S segundos sin avanzar, lo marca en estado como
# "atascado" y fuerza una reconexión (pues la causa más común es que
# la conexión quedó colgada en una llamada de red que _con_timeout no
# pudo recuperar — típico cuando el socket subyacente se muere).
_heartbeat_por_par = {}     # {par: float epoch}
_heartbeat_lock    = threading.Lock()
WATCHDOG_PAR_STUCK_S = 600  # 10 min = 10 velas M1 sin avanzar = algo pasa
WATCHDOG_DEBOUNCE_S  = 300  # no forzar reconexión más de 1 vez cada 5 min
# ── Auto-calibración: modo IFC propio por par (p.ej. GBPCAD continuidad
# y GBPCHF reversión a la vez), elegido por backtest real al iniciar. ──
_modo_por_par = {}
# ── Lista negra de la sesión: pares banqueados por mal desempeño en vivo ──
_lista_negra = set()

# =============================================================
# CONFIG
# =============================================================
def cargar_config():
    global config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config.update(json.load(f))

def guardar_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _faltan_credenciales(cfg):
    """Devuelve un mensaje de error si faltan credenciales para el bróker
    activo, o "" si están completas. Deriv usa token; IQ usa usuario+clave."""
    if broker_actual(cfg) == "deriv":
        if not (cfg.get("deriv_token") or "").strip():
            return ("Falta el token de Deriv. En el panel, pega tu token de "
                    "Deriv (Settings → API token) y guarda la configuración.")
        return ""
    usuario  = (cfg.get("usuario") or "").strip()
    password = cfg.get("password") or ""
    if not usuario or not password:
        return ("Falta email o contraseña de IQ Option. Escríbelos en "
                "Configuración y guarda (o inicia el bot una vez).")
    return ""


# =============================================================
# SESIÓN PERSISTENTE (contadores del día en disco)
# =============================================================
# Motivo: si el usuario detiene el bot para ajustar algo y lo vuelve a
# iniciar el MISMO día, quiere seguir donde estaba (wins, losses, neto,
# operaciones, racha, bloqueos), no reiniciar los contadores del día. Al
# cambiar la fecha, la sesión guardada se ignora — nuevo día = todo a 0.
# Un botón "Reiniciar día" permite forzar el reseteo manualmente.

def _fecha_hoy():
    return datetime.date.today().isoformat()

def guardar_sesion():
    """Snapshot atómico del estado del día. Se llama tras cada operación
    cerrada (ganada/perdida/empate) y en momentos clave para que un
    Detener + Iniciar rápido continúe donde estaba.
    NUNCA lanza excepción hacia arriba — si falla, se registra y sigue."""
    if not config.get("sesion_persistente", True):
        return
    try:
        with _lock:
            data = {
                "fecha":             _fecha_hoy(),
                "wins":              int(estado.get("wins", 0)),
                "losses":            int(estado.get("losses", 0)),
                "neto":              float(estado.get("neto", 0.0)),
                "perdidas_seguidas": int(estado.get("perdidas_seguidas", 0)),
                "operaciones":       list(estado.get("operaciones", []))[:500],
                "pares_bloqueados":  dict(estado.get("pares_bloqueados", {})),
            }
        tmp = SESION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, SESION_FILE)
    except Exception as e:
        # No usamos log() aquí para no arriesgar recursión con el _lock
        print(f"[SESION] Error guardando sesión: {e}")

def cargar_sesion():
    """Restaura contadores del día si hay una sesión guardada de HOY.
    Si es de otro día (o no existe), no toca nada y devuelve False.
    Devuelve True si se restauró algo."""
    if not config.get("sesion_persistente", True):
        return False
    if not os.path.exists(SESION_FILE):
        return False
    try:
        with open(SESION_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[SESION] No pude leer {SESION_FILE}: {e}")
        return False
    if data.get("fecha") != _fecha_hoy():
        return False   # sesión de otro día → nuevo día empieza en cero
    with _lock:
        estado["wins"]              = int(data.get("wins", 0))
        estado["losses"]            = int(data.get("losses", 0))
        estado["neto"]              = float(data.get("neto", 0.0))
        estado["perdidas_seguidas"] = int(data.get("perdidas_seguidas", 0))
        estado["operaciones"]       = list(data.get("operaciones", []))
        estado["pares_bloqueados"]  = dict(data.get("pares_bloqueados", {}))
    return True

def reiniciar_sesion():
    """Borra la sesión guardada y resetea contadores del día a cero. Se
    llama desde la UI (botón 'Reiniciar día') y al iniciar un día nuevo
    detectado automáticamente."""
    with _lock:
        estado["wins"]              = 0
        estado["losses"]            = 0
        estado["neto"]              = 0.0
        estado["perdidas_seguidas"] = 0
        estado["operaciones"]       = []
        estado["pares_bloqueados"]  = {}
    try:
        if os.path.exists(SESION_FILE):
            os.remove(SESION_FILE)
    except Exception as e:
        print(f"[SESION] No pude borrar {SESION_FILE}: {e}")


# =============================================================
# HISTORIAL PERMANENTE (CSV externo) + ESTADÍSTICAS
# =============================================================
# Cada operación cerrada se agrega como una fila al CSV permanente. Este
# archivo NO se resetea al cambiar de día ni con "Reiniciar día" — es el
# registro histórico que el usuario conserva y abre en Excel, y la fuente
# del Excel de estadísticas desglosadas. No toca la estrategia: es solo
# escritura de lo que el bot ya calcula al cerrar cada operación.

def _franja_hora_csv(hora_str):
    """'HH:MM:SS' -> 'HH:00-HH:59' (franja de una hora, como en la UI)."""
    try:
        h = int(hora_str.split(":")[0])
        return f"{h:02d}:00-{h:02d}:59"
    except Exception:
        return "??"

def registrar_operacion_historial(op):
    """Agrega UNA operación al CSV permanente. 'op' es el dict que el bot
    construye al cerrar (par, direccion, resultado, monto, ganancia,
    balance, fecha 'YYYY-MM-DD HH:MM:SS'; opcionalmente estrategia).
    Crea el archivo con cabecera la primera vez. Nunca lanza hacia
    arriba: si falla, se registra y el bot sigue operando."""
    try:
        fecha_hora = op.get("fecha", "")
        partes = fecha_hora.split(" ")
        fecha = partes[0] if partes else ""
        hora = partes[1] if len(partes) > 1 else ""
        try:
            d = datetime.datetime.strptime(fecha, "%Y-%m-%d").date()
            dia_semana = _DIAS_ES[d.weekday()]
        except Exception:
            dia_semana = ""
        fila = {
            "timestamp": fecha_hora,
            "fecha": fecha,
            "hora": hora,
            "dia_semana": dia_semana,
            "franja_horaria": _franja_hora_csv(hora),
            "par": op.get("par", ""),
            "estrategia": op.get("estrategia", config.get("estrategia", "")),
            "direccion": op.get("direccion", ""),
            "resultado": op.get("resultado", ""),
            "monto": op.get("monto", 0),
            "ganancia": op.get("ganancia", 0),
            "balance": op.get("balance", 0),
            "racha_perdidas_momento": op.get("racha_perdidas_momento", ""),
        }
        with _historial_lock:
            existe = os.path.exists(HISTORIAL_CSV)
            with open(HISTORIAL_CSV, "a", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(f, fieldnames=_HISTORIAL_COLUMNAS)
                if not existe:
                    w.writeheader()
                w.writerow(fila)
    except Exception as e:
        print(f"[HISTORIAL] No pude registrar la operación: {e}")

def _leer_historial():
    """Devuelve todas las filas del CSV permanente como lista de dicts.
    Lista vacía si no existe o hay error."""
    if not os.path.exists(HISTORIAL_CSV):
        return []
    try:
        with _historial_lock:
            with open(HISTORIAL_CSV, "r", newline="", encoding="utf-8") as f:
                return list(_csv.DictReader(f))
    except Exception as e:
        print(f"[HISTORIAL] No pude leer el historial: {e}")
        return []

def _num(x, tipo=float):
    try:
        return tipo(x)
    except Exception:
        return tipo(0)

def _stats_por_clave(filas, clave):
    """Agrupa filas por el valor de 'clave' y calcula ganadas/perdidas/
    empates/total/efectividad/neto por grupo. Devuelve lista de dicts
    ordenada por total desc."""
    grupos = {}
    for r in filas:
        k = r.get(clave, "") or "(vacío)"
        g = grupos.setdefault(k, {"clave": k, "ganadas": 0, "perdidas": 0,
                                  "empates": 0, "total": 0, "neto": 0.0})
        res = r.get("resultado", "")
        if res == "win":
            g["ganadas"] += 1
        elif res == "loss":
            g["perdidas"] += 1
        else:
            g["empates"] += 1
        g["total"] += 1
        g["neto"] += _num(r.get("ganancia", 0))
    out = []
    for g in grupos.values():
        decididas = g["ganadas"] + g["perdidas"]
        g["efectividad"] = round(g["ganadas"] / decididas * 100, 1) if decididas else 0.0
        g["neto"] = round(g["neto"], 2)
        out.append(g)
    out.sort(key=lambda x: x["total"], reverse=True)
    return out

def _racha_maxima(filas, tipo):
    """Racha consecutiva máxima de 'win' o 'loss' en orden cronológico."""
    filas_ord = sorted(filas, key=lambda r: r.get("timestamp", ""))
    mejor = actual = 0
    for r in filas_ord:
        if r.get("resultado", "") == tipo:
            actual += 1
            mejor = max(mejor, actual)
        else:
            actual = 0
    return mejor

def calcular_estadisticas():
    """Resumen completo desglosado desde el CSV permanente. Para la
    pestaña de la UI y el Excel. Solo lectura — no toca nada."""
    filas = _leer_historial()
    total = len(filas)
    if total == 0:
        return {"ok": True, "vacio": True, "total": 0}
    ganadas = sum(1 for r in filas if r.get("resultado") == "win")
    perdidas = sum(1 for r in filas if r.get("resultado") == "loss")
    empates = total - ganadas - perdidas
    decididas = ganadas + perdidas
    neto = round(sum(_num(r.get("ganancia", 0)) for r in filas), 2)
    fechas = sorted({r.get("fecha", "") for r in filas if r.get("fecha")})
    return {
        "ok": True, "vacio": False,
        "total": total, "ganadas": ganadas, "perdidas": perdidas, "empates": empates,
        "efectividad": round(ganadas / decididas * 100, 1) if decididas else 0.0,
        "neto": neto,
        "racha_ganadas_max": _racha_maxima(filas, "win"),
        "racha_perdidas_max": _racha_maxima(filas, "loss"),
        "dias_operados": len(fechas),
        "primer_dia": fechas[0] if fechas else "",
        "ultimo_dia": fechas[-1] if fechas else "",
        "por_par": _stats_por_clave(filas, "par"),
        "por_hora": sorted(_stats_por_clave(filas, "franja_horaria"), key=lambda x: x["clave"]),
        "por_dia_semana": _stats_por_clave(filas, "dia_semana"),
        "por_dia": sorted(_stats_por_clave(filas, "fecha"), key=lambda x: x["clave"]),
        "por_estrategia": _stats_por_clave(filas, "estrategia"),
    }


def generar_excel_estadisticas():
    """Construye el .xlsx de estadísticas desglosadas desde el historial
    permanente. Devuelve (bytes_xlsx, nombre_sugerido) o (None, error).
    Hojas: Resumen, Operaciones (crudo), Por par, Por hora, Por día
    semana, Por día, Por estrategia. Solo lectura — no toca estrategia."""
    try:
        from excel_export import construir_xlsx
    except Exception as e:
        return None, f"No pude cargar el generador de Excel: {e}"
    filas = _leer_historial()
    if not filas:
        return None, "Todavía no hay operaciones registradas en el historial."
    est = calcular_estadisticas()

    def tabla_stats(titulo_clave, grupos):
        out = [[titulo_clave, "Ganadas", "Perdidas", "Empates", "Total",
                "Efectividad %", "Neto $"]]
        for g in grupos:
            out.append([g["clave"], g["ganadas"], g["perdidas"], g["empates"],
                        g["total"], g["efectividad"], g["neto"]])
        return out

    # Hoja Resumen
    resumen = [
        ["ESTADÍSTICAS DEL BOT — RESUMEN GENERAL", ""],
        ["Generado", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["", ""],
        ["Total operaciones", est["total"]],
        ["Ganadas", est["ganadas"]],
        ["Perdidas", est["perdidas"]],
        ["Empates", est["empates"]],
        ["Efectividad %", est["efectividad"]],
        ["Neto acumulado $", est["neto"]],
        ["", ""],
        ["Racha máx. ganadas", est["racha_ganadas_max"]],
        ["Racha máx. perdidas", est["racha_perdidas_max"]],
        ["", ""],
        ["Días operados", est["dias_operados"]],
        ["Primer día", est["primer_dia"]],
        ["Último día", est["ultimo_dia"]],
    ]

    # Hoja Operaciones (crudo, todas las columnas del historial)
    operaciones = [list(_HISTORIAL_COLUMNAS)]
    for r in filas:
        operaciones.append([r.get(c, "") for c in _HISTORIAL_COLUMNAS])

    hojas = [
        ("Resumen", resumen),
        ("Operaciones", operaciones),
        ("Por par", tabla_stats("Par", est["por_par"])),
        ("Por hora", tabla_stats("Franja horaria", est["por_hora"])),
        ("Por día semana", tabla_stats("Día", est["por_dia_semana"])),
        ("Por día", tabla_stats("Fecha", est["por_dia"])),
        ("Por estrategia", tabla_stats("Estrategia", est["por_estrategia"])),
    ]
    try:
        data = construir_xlsx(hojas)
    except Exception as e:
        return None, f"Error generando el Excel: {e}"
    nombre = f"estadisticas_bot_{datetime.date.today().isoformat()}.xlsx"
    return data, nombre


# =============================================================
# LOG
# =============================================================
def log(msg):
    ts    = datetime.datetime.now().strftime("%H:%M:%S")
    linea = f"[{ts}] {msg}"
    print(linea)
    with _lock:
        estado["log"].insert(0, linea)
        if len(estado["log"]) > 150:
            estado["log"] = estado["log"][:150]

# =============================================================
# TELEGRAM
# =============================================================

_telegram_errores_avisados = set()   # para loguear cada motivo UNA vez
_tg_modo_red = {"trust_env": False}  # se recuerda el modo que funcionó

def _tg_request(metodo, url, timeout, **kw):
    """Petición HTTP a la API de Telegram con FALLBACK de proxy.
    Primero intenta en el modo que funcionó la última vez (por defecto
    ignorando proxies del sistema, trust_env=False). Si falla por
    conexión/proxy/SSL, reintenta en el modo contrario — hay redes,
    VPNs y antivirus que EXIGEN pasar por el proxy del sistema, y con
    trust_env=False fijo esas conexiones morían siempre.
    Devuelve (resp, None) o (None, descripcion_del_error)."""
    ultimo_err = None
    primero = bool(_tg_modo_red["trust_env"])
    for trust in (primero, not primero):
        try:
            s = requests.Session()
            s.trust_env = trust
            resp = s.request(metodo, url, timeout=timeout, **kw)
            _tg_modo_red["trust_env"] = trust   # recordar lo que funcionó
            return resp, None
        except requests.exceptions.SSLError as e:
            ultimo_err = (f"Error SSL hacia Telegram ({e.__class__.__name__}). "
                          f"Suele ser un antivirus/proxy interceptando HTTPS. Detalle: {e}")
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError) as e:
            ultimo_err = (f"Sin conexión con api.telegram.org "
                          f"({e.__class__.__name__}) con trust_env={trust}. Detalle: {e}")
        except requests.exceptions.Timeout:
            ultimo_err = "Tiempo de espera agotado hacia api.telegram.org."
        except Exception as e:
            ultimo_err = f"{e.__class__.__name__}: {e}"
    return None, ultimo_err

def enviar_telegram(msg):
    """Envía un mensaje al chat de Telegram configurado.
    Devuelve (ok, detalle). Antes se ignoraba la respuesta de la API de
    Telegram: si el token era inválido, el chat_id estaba mal o el bot
    estaba bloqueado, el envío fallaba EN SILENCIO TOTAL — imposible de
    diagnosticar. Ahora se lee la respuesta y se loguea el motivo exacto
    (una vez por tipo de error, para no llenar el log)."""
    tok = config.get("token_telegram", "").strip()
    cid = config.get("chat_id", "").strip()
    if not tok or not cid:
        return False, "Token o chat_id no configurados."
    resp, err_red = _tg_request(
        "POST", f"https://api.telegram.org/bot{tok}/sendMessage",
        timeout=6,
        data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
    )
    if resp is None:
        detalle = err_red or "Error de red desconocido."
        if detalle not in _telegram_errores_avisados:
            _telegram_errores_avisados.add(detalle)
            log(f"[TELEGRAM] ❌ {detalle}")
        return False, detalle
    try:
        data = resp.json()
    except Exception:
        data = {}
    if data.get("ok"):
        return True, "Enviado."
    # La API rechazó el mensaje: traducir el motivo a algo accionable.
    desc = str(data.get("description", f"HTTP {resp.status_code}"))
    d = desc.lower()
    if "unauthorized" in d:
        detalle = "Token inválido (Unauthorized). Revisa el token de @BotFather."
    elif "chat not found" in d:
        detalle = ("chat_id incorrecto o nunca le diste /start al bot. "
                   "Abre tu bot en Telegram, mándale /start y verifica el chat_id.")
    elif "bot was blocked" in d:
        detalle = "El bot está bloqueado en tu Telegram. Desbloquéalo y mándale /start."
    elif "parse" in d or "entit" in d:
        detalle = f"Telegram rechazó el formato HTML del mensaje: {desc}"
    else:
        detalle = f"Telegram rechazó el envío: {desc}"
    if detalle not in _telegram_errores_avisados:
        _telegram_errores_avisados.add(detalle)
        log(f"[TELEGRAM] ❌ {detalle}")
    return False, detalle


def probar_telegram(tok=None, cid=None):
    """Diagnóstico de Telegram en DOS pasos, con las credenciales dadas
    (o las de config si no vienen) SIN guardarlas:
      Paso 1 — getMe: valida conectividad + token (sin tocar el chat).
      Paso 2 — sendMessage: valida el chat_id enviando la prueba real.
    Así el resultado dice exactamente DÓNDE falla: red, token o chat.
    Usa _tg_request (con fallback de proxy). Para el botón de la UI."""
    tok = (tok if tok is not None else config.get("token_telegram", "")).strip()
    cid = (cid if cid is not None else config.get("chat_id", "")).strip()
    if not tok:
        return {"ok": False, "detalle": "Falta el token. Créalo con @BotFather en Telegram."}
    if not cid:
        return {"ok": False, "detalle": "Falta el chat_id. Escríbele a @userinfobot en Telegram para obtener tu ID."}

    # ── Paso 1: conectividad + token ──────────────────────────────
    resp, err_red = _tg_request("GET", f"https://api.telegram.org/bot{tok}/getMe", timeout=8)
    if resp is None:
        return {"ok": False, "detalle": f"PASO 1 (conexión): {err_red}"}
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not data.get("ok"):
        desc = str(data.get("description", f"HTTP {resp.status_code}"))
        if "unauthorized" in desc.lower():
            return {"ok": False, "detalle": "PASO 1 (token): token inválido (Unauthorized). Cópialo completo desde @BotFather."}
        return {"ok": False, "detalle": f"PASO 1 (token): Telegram respondió: {desc}"}
    bot_user = (data.get("result") or {}).get("username", "?")

    # ── Paso 2: el chat ───────────────────────────────────────────
    resp2, err_red2 = _tg_request(
        "POST", f"https://api.telegram.org/bot{tok}/sendMessage", timeout=8,
        data={"chat_id": cid,
              "text": "✅ <b>Prueba de Telegram OK</b>\nEl bot puede enviarte mensajes.",
              "parse_mode": "HTML"},
    )
    if resp2 is None:
        return {"ok": False, "detalle": f"PASO 2 (envío): {err_red2}"}
    try:
        data2 = resp2.json()
    except Exception:
        data2 = {}
    if data2.get("ok"):
        return {"ok": True, "detalle": f"Conectado como @{bot_user}. Mensaje de prueba enviado — revisa tu Telegram."}
    desc = str(data2.get("description", f"HTTP {resp2.status_code}"))
    d = desc.lower()
    if "chat not found" in d:
        return {"ok": False, "detalle": f"PASO 2 (chat): el token funciona (bot @{bot_user}) pero Telegram no encuentra el chat {cid}. Dos causas típicas: el chat_id está mal, o nunca le diste /start a @{bot_user}. Ábrelo, mándale /start y prueba otra vez."}
    if "bot was blocked" in d:
        return {"ok": False, "detalle": f"PASO 2 (chat): tienes bloqueado a @{bot_user}. Desbloquéalo y mándale /start."}
    return {"ok": False, "detalle": f"PASO 2 (envío): Telegram respondió: {desc}"}


def _telegram_get_updates(tok, offset):
    """
    Pide actualizaciones a Telegram con timeout bajo.
    Devuelve lista de updates o [] si hay error de red.
    NO propaga excepciones — el loop nunca se frena por errores de conexión.
    Usa _tg_request (mismo fallback de proxy que el envío de mensajes).
    """
    resp, _err = _tg_request(
        "GET", f"https://api.telegram.org/bot{tok}/getUpdates",
        timeout=8,
        params={"offset": offset + 1, "timeout": 5},
    )
    if resp is None:
        return []   # sin red / proxy / timeout — no es un error del bot
    try:
        data = resp.json()
    except Exception:
        return []
    if not data.get("ok"):
        return []
    return data.get("result", [])


def _telegram_comandos_loop():
    """
    Hilo en segundo plano que escucha /estado desde Telegram.
    Comandos /encender y /apagar eliminados.
    Resistente a cortes de conexión: nunca frena el bot.
    """
    telegram_offset = 0
    log("[TELEGRAM] Escucha de /estado iniciada.")
    while True:
        try:
            tok = config.get("token_telegram", "").strip()
            cid_autorizado = config.get("chat_id", "").strip()
            if not tok or not cid_autorizado:
                time.sleep(10)
                continue

            updates = _telegram_get_updates(tok, telegram_offset)
            for upd in updates:
                telegram_offset = max(telegram_offset, upd.get("update_id", 0))
                msg_data = upd.get("message", {})
                texto    = (msg_data.get("text") or "").strip().lower()
                cid_msg  = str(msg_data.get("chat", {}).get("id", ""))

                if not texto.startswith("/"):
                    continue
                if cid_msg != str(cid_autorizado):
                    continue

                if texto in ("/estado", "/status"):
                    bloqueados = estado.get("pares_bloqueados", {})
                    ahora_ts   = time.time()
                    info_bloq  = ""
                    for p, ts in list(bloqueados.items()):
                        if ts > ahora_ts:
                            mins = int((ts - ahora_ts) / 60) + 1
                            info_bloq += f"\n🚫 {p}: bloqueado {mins} min más"
                    if estado["corriendo"]:
                        enviar_telegram(
                            f"🟢 <b>Bot corriendo</b>\n"
                            f"Balance: ${estado.get('balance',0):.2f}\n"
                            f"Wins: {estado.get('wins',0)} | Losses: {estado.get('losses',0)}"
                            + info_bloq
                        )
                    else:
                        enviar_telegram("🔴 Bot apagado." + info_bloq)

                elif texto in ("/resumen", "/desempeno", "/desempeño"):
                    enviar_telegram(texto_resumen_desempeno())

        except Exception as e:
            log(f"[TELEGRAM] Error en escucha de comandos: {e}")

        # Esperar 3s entre polls; si hay error de red el bucle simplemente
        # vuelve a intentarlo en la próxima iteración.
        time.sleep(3)

# =============================================================
# FILTRO DE PAR BLOQUEADO
# =============================================================

def par_esta_bloqueado(par):
    """Devuelve True si el par está en cuarentena."""
    with _lock:
        ts_desbloqueo = estado["pares_bloqueados"].get(par, 0)
    return time.time() < ts_desbloqueo

def bloquear_par(par):
    """Bloquea un par por 'par_bloqueo_minutos' minutos."""
    minutos = config.get("par_bloqueo_minutos", 60)
    ts = time.time() + minutos * 60
    with _lock:
        estado["pares_bloqueados"][par] = ts
    desbloqueo_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
    log(f"[BLOQUEO] 🚫 {par} bloqueado por {minutos} min (hasta {desbloqueo_str})")
    guardar_sesion()   # persistir el bloqueo por si se reinicia el bot
    enviar_telegram(
        f"🚫 <b>PAR BLOQUEADO</b>\n"
        f"Par: <b>{par}</b>\n"
        f"Motivo: 3 pérdidas seguidas\n"
        f"Reanuda a las: <b>{desbloqueo_str}</b>"
    )


# =============================================================
# REPORTES DE DESEMPEÑO (por franja horaria y por par)
# =============================================================
# Se calculan a partir de estado["operaciones"], que ya se llena en
# cada cierre de operación real (no es un sistema de tracking nuevo,
# es un análisis de los datos que el bot ya guarda).

def _franja_horaria(hora):
    """Agrupa la hora del día (0-23) en franjas legibles de 3 horas."""
    inicio = (hora // 3) * 3
    fin = inicio + 3
    return f"{inicio:02d}:00-{fin:02d}:00"


def calcular_desempeno_por_horario(operaciones=None):
    """
    Devuelve un dict {franja_horaria: {wins, losses, neto, total, winrate}}
    a partir del historial real de operaciones cerradas.
    """
    if operaciones is None:
        with _lock:
            operaciones = list(estado["operaciones"])

    resumen = {}
    for op in operaciones:
        try:
            hora = datetime.datetime.strptime(op["fecha"], "%Y-%m-%d %H:%M:%S").hour
        except Exception:
            continue
        franja = _franja_horaria(hora)
        r = resumen.setdefault(franja, {"wins": 0, "losses": 0, "neto": 0.0})
        if op["resultado"] == "win":
            r["wins"] += 1
        else:
            r["losses"] += 1
        r["neto"] += op.get("ganancia", 0.0)

    # Completar total y winrate, y ordenar por franja
    for r in resumen.values():
        r["total"] = r["wins"] + r["losses"]
        r["winrate"] = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0.0
        r["neto"] = round(r["neto"], 2)

    return dict(sorted(resumen.items()))


def calcular_desempeno_por_par(operaciones=None):
    """
    Devuelve un dict {par: {wins, losses, neto, total, winrate}}
    a partir del historial real de operaciones cerradas, ordenado de
    mejor a peor neto (el más rentable primero).
    """
    if operaciones is None:
        with _lock:
            operaciones = list(estado["operaciones"])

    resumen = {}
    for op in operaciones:
        par = op.get("par", "?")
        r = resumen.setdefault(par, {"wins": 0, "losses": 0, "neto": 0.0})
        if op["resultado"] == "win":
            r["wins"] += 1
        else:
            r["losses"] += 1
        r["neto"] += op.get("ganancia", 0.0)

    for r in resumen.values():
        r["total"] = r["wins"] + r["losses"]
        r["winrate"] = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0.0
        r["neto"] = round(r["neto"], 2)

    # Ordenar de mejor a peor neto
    return dict(sorted(resumen.items(), key=lambda kv: kv[1]["neto"], reverse=True))


def texto_resumen_desempeno():
    """Construye el texto formateado (para log/Telegram) con ambos desgloses."""
    por_par = calcular_desempeno_por_par()
    por_horario = calcular_desempeno_por_horario()

    if not por_par:
        return "📊 Aún no hay operaciones registradas en esta sesión."

    txt = "📊 <b>Desempeño por PAR</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for par, r in por_par.items():
        signo = "+" if r["neto"] >= 0 else ""
        txt += (f"{'🟢' if r['neto'] >= 0 else '🔴'} <b>{par}</b>: "
                f"{r['wins']}W/{r['losses']}L ({r['winrate']}%) | {signo}${r['neto']}\n")

    txt += "\n📊 <b>Desempeño por HORARIO</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for franja, r in por_horario.items():
        signo = "+" if r["neto"] >= 0 else ""
        txt += (f"{'🟢' if r['neto'] >= 0 else '🔴'} <b>{franja}</b>: "
                f"{r['wins']}W/{r['losses']}L ({r['winrate']}%) | {signo}${r['neto']}\n")

    return txt


def _enviar_ranking_warmup():
    """
    Se llama una sola vez, cuando TODOS los pares configurados ya
    terminaron su testeo previo (warmup). Manda un único mensaje
    consolidado comparando la tasa de acierto simulada de cada par,
    de mejor a peor, para que el usuario pueda elegir con cuáles
    operar ese momento.

    IMPORTANTE: esta tasa viene de una simulación simplificada sobre
    pocas velas recientes (ver warmup más arriba) — es una comparación
    relativa entre pares en ESE momento, no una garantía de resultado.
    """
    with _lock:
        resultados = dict(estado["warmup_resultados"])

    # Ordenar: primero los que tienen señales y mejor tasa; los pares
    # sin señales detectadas (tasa None) van al final.
    def _orden(item):
        _, r = item
        return (r["tasa"] is None, -(r["tasa"] or 0))

    ordenados = sorted(resultados.items(), key=_orden)

    txt = (
        "🏁 <b>Testeo previo completado — todos los pares</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Ranking de la simulación previa (mayor a menor acierto):\n\n"
    )
    for par, r in ordenados:
        if r["total"] == 0:
            txt += f"⚪ <b>{par}</b>: sin señales en el período | tendencia:{r['tendencia']}\n"
            continue
        emoji = "🟢" if r["tasa"] >= 60 else ("🟡" if r["tasa"] >= 45 else "🔴")
        txt += (f"{emoji} <b>{par}</b>: {r['wins']}W/{r['losses']}L "
                f"({r['tasa']}%) | tendencia:{r['tendencia']}"
                f"{'' if r['mercado_activo'] else ' ⚠️ baja volatilidad'}\n")

    txt += (
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ Esto es una simulación rápida sobre pocas velas recientes, "
        "no una garantía — úsalo como apoyo para elegir con qué pares "
        "operar ahora, no como predicción exacta."
    )
    log("[WARMUP] Ranking consolidado de pares enviado.")
    enviar_telegram(txt)

# =============================================================
# INDICADORES
# =============================================================

def calcular_ema(source, period):
    """Calculate EMA from a list of candles (dicts) or floats."""
    if not source:
        return None
    closes = [v["close"] for v in source] if isinstance(source[0], dict) else list(source)
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def calcular_ema_series(closes, period):
    """Return the full EMA series as a list of floats."""
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    result = [ema]
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
        result.append(ema)
    return result

def calcular_rsi(velas, periodo=14):
    """Standard RSI calculation."""
    if len(velas) < periodo + 1:
        return None
    closes = [v["close"] for v in velas]
    cambios = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    ganancias = [max(c, 0) for c in cambios]
    perdidas = [abs(min(c, 0)) for c in cambios]
    avg_g = sum(ganancias[:periodo]) / periodo
    avg_p = sum(perdidas[:periodo]) / periodo
    for i in range(periodo, len(cambios)):
        avg_g = (avg_g * (periodo - 1) + ganancias[i]) / periodo
        avg_p = (avg_p * (periodo - 1) + perdidas[i]) / periodo
    if avg_p == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_p)), 2)

def calcular_heiken_ashi(velas):
    ha = []
    for i, v in enumerate(velas):
        ha_close = (v["open"] + v["max"] + v["min"] + v["close"]) / 4
        if i == 0:
            ha_open = (v["open"] + v["close"]) / 2
        else:
            ha_open = (ha[i-1]["open"] + ha[i-1]["close"]) / 2
        ha.append({
            "open": ha_open,
            "close": ha_close,
            "max": max(v["max"], ha_open, ha_close),
            "min": min(v["min"], ha_open, ha_close),
        })
    return ha

def detectar_sr(velas, min_toques=None):
    lookback = config["sr_lookback"]
    zone_pips = config["sr_zone_pips"]
    if min_toques is None:
        min_toques = config["sr_touch_min"]
    subset = velas[-lookback:] if len(velas) >= lookback else velas
    candidates = [v["max"] for v in subset] + [v["min"] for v in subset]
    levels = []
    for price in candidates:
        found = False
        for lvl in levels:
            if abs(lvl["price"] - price) <= zone_pips:
                lvl["toques"] += 1
                lvl["price"] = (lvl["price"] + price) / 2
                found = True
                break
        if not found:
            levels.append({"price": price, "toques": 1})
    return [lvl["price"] for lvl in levels if lvl["toques"] >= min_toques]

def cerca_numero_redondo(precio):
    zona = config["round_zone"]
    remainder = round(precio % 0.0050, 6)
    return remainder < zona or remainder > (0.0050 - zona)

def cerca_nivel_sr(precio, sr_levels):
    zona = config["sr_zone_pips"]
    return any(abs(precio - lvl) <= zona for lvl in sr_levels)

def color_ha(hv):
    return "verde" if hv["close"] >= hv["open"] else "roja"

def segundos_para_nueva_vela():
    now = datetime.datetime.utcnow()
    return 60 - now.second - now.microsecond / 1_000_000

def esperar_apertura_vela(buffer_ms=0):
    """
    Espera con precisión hasta el segundo 00 exacto de la siguiente vela.
    Usa sleep grueso para la espera larga y bucle fino los últimos 50ms
    para garantizar entrada en el segundo 00, no en el 59 ni en el 02.

    TIMING-FIX: reducido el buffer de 0.10s a 0.05s, y el bucle fino de
    5ms a 1ms. En Windows, time.sleep() tiene resolución ~15ms por defecto,
    así que el sleep grueso puede tardarse 50-100ms. El bucle fino de 1ms
    atrapa el segundo 00 con precisión sub-5ms.
    """
    espera = segundos_para_nueva_vela() - 0.05
    if espera > 0:
        time.sleep(espera)
    while True:
        now = datetime.datetime.utcnow()
        if now.second == 0:
            break
        time.sleep(0.001)

def esperar_hasta_segundo(objetivo):
    """Duerme hasta el segundo 'objetivo' (0-59.9) de la vela ACTUAL; si
    ya pasó, hasta ese mismo segundo de la vela siguiente."""
    now = datetime.datetime.utcnow()
    s = now.second + now.microsecond / 1_000_000
    delta = (objetivo - s) % 60
    if delta > 0.01:
        time.sleep(delta)


# ── Coordinador de entradas: una sola compra por vela, la mejor ──────
def _prioridad_senal(velas, det):
    """Prioridad de una señal con lo medible al detectarla. Para IFC:
    cuerpo de la fuerza (+10 si mismo color). Para Facundo: cuerpo de la
    vela de confirmación (+3 por toque extra del nivel, hasta +15). Para
    otras (nivel): calidad del evento. Todas suman ADX (0-20).
    Devuelve (prioridad, desglose_txt)."""
    det = det or {}
    if "cuerpo_fuerza" in det:                       # IFC
        base = float(det.get("cuerpo_fuerza", 0))
        bono = 10.0 if det.get("mismo_color") else 0.0
        etiqueta = f"cuerpo {base:.0f} + color {bono:.0f}"
    elif "cuerpo_conf" in det:                       # FACUNDO
        base = float(det.get("cuerpo_conf", 0))
        # Nivel más probado = señal más confiable. AJUSTE v9.1.2: bono
        # reforzado a +15 por toque extra (más allá del 3ro), con tope en
        # +30. Esto hace que cuando dos pares señalan en la misma vela, el
        # del nivel con 4+ toques gane claramente la prioridad sobre el
        # del 3er toque, incluso si su cuerpo de confirmación es hasta 15
        # puntos menor. Regla del usuario: "si hay una entrada con 3 toques
        # para entrar en el 4to, tomar esta como prioridad".
        n_toques = int(det.get("toques", 0))
        toques_extra = max(0, n_toques - 3)
        bono = min(30.0, toques_extra * 15.0)
        # OJO con el naming: aquí se muestra el nº de toque REAL del nivel
        # (toque #N, ya validado por la regla del 3er toque en el detector)
        # y el BONO de puntos por separado. Antes decía "toques 0" cuando
        # el bono era 0 (toque #3 exacto) y parecía que el nivel tenía 0
        # toques — confusión reportada por el usuario.
        etiqueta = f"conf {base:.0f} + bono +{bono:.0f} por toque #{n_toques}"
    else:                                            # nivel (u otra)
        base = float(det.get("calidad", 70))
        bono = 0.0
        etiqueta = f"calidad {base:.0f}"
    adx = calcular_adx(velas, 14)
    pts_adx = min(max(adx, 0.0), 40.0) / 2.0 if adx is not None else 0.0
    prioridad = base + bono + pts_adx
    desglose = f"{etiqueta} + adx {pts_adx:.0f}"
    return prioridad, desglose


def _registrar_candidato(minuto, par, prioridad):
    """Registra la señal de 'par' como candidata a entrar en la vela que
    abre al final de 'minuto'. Limpia minutos viejos."""
    with _entrada_lock:
        e = _entradas_por_minuto.setdefault(minuto, {"candidatos": {}, "ganador": None})
        e["candidatos"][par] = prioridad
        for m in [m for m in _entradas_por_minuto if m < minuto - 3]:
            _entradas_por_minuto.pop(m, None)


def _soy_ganador(minuto, par):
    """Al segundo 00, decide si 'par' es el ganador de la vela. La regla
    correcta es: el de MAYOR prioridad entre los candidatos REGISTRADOS
    hasta el instante en que se decide. Si el ganador ya estaba decidido,
    un candidato que llegue tarde (tolerancia) cede automáticamente.

    TIMING-FIX: antes había un time.sleep(0.20) aquí "para que lleguen
    todos los candidatos". Pero los pares analizan en paralelo y registran
    su candidatura ANTES del segundo 00 (al :57), así que esa espera era
    innecesaria y causaba 200ms de retraso en CADA entrada. Eliminada:
    decidimos inmediatamente con los candidatos ya registrados.
    Devuelve (soy_ganador, ganador, candidatos)."""
    with _entrada_lock:
        e = _entradas_por_minuto.get(minuto)
        if not e or par not in e["candidatos"]:
            return False, None, {}
        if e["ganador"] is None:
            e["ganador"] = max(e["candidatos"], key=e["candidatos"].get)
        g = e["ganador"]
        return g == par, g, dict(e["candidatos"])


def _heartbeat(par):
    """Anota el timestamp actual como 'última iteración completada' del par.
    Lo llama loop_par al final de cada vuelta del bucle. Lo usa el watchdog
    para detectar hilos atascados."""
    with _heartbeat_lock:
        _heartbeat_por_par[par] = time.time()


def _heartbeat_quitar(par):
    """Quita el heartbeat de un par (cuando su hilo termina)."""
    with _heartbeat_lock:
        _heartbeat_por_par.pop(par, None)


def _watchdog_loop(mi_run_id):
    """Hilo watchdog: revisa cada 60s los heartbeats de los pares. Si un
    par lleva más de WATCHDOG_PAR_STUCK_S segundos sin avanzar, lo marca
    como atascado en el estado y fuerza una reconexión limpia del socket
    (causa más probable de un hilo colgado: socket muerto).

    DEBOUNCE: no fuerza reconexión más de 1 vez cada WATCHDOG_DEBOUNCE_S
    segundos, para evitar tormentas de reconexión que rompen el bot.
    Solo dispara si HAY pares monitoreados (evita falsos positivos cuando
    los pares están en warmup o recién arrancando)."""
    _ultima_reconexion_forzada_por_watchdog = 0.0
    while True:
        time.sleep(60)
        try:
            if _run_id != mi_run_id:
                return
            with _lock:
                if not estado.get("corriendo"):
                    return
            ahora = time.time()
            with _heartbeat_lock:
                snapshot = dict(_heartbeat_por_par)
            if not snapshot:
                continue   # sin pares monitoreados aún (todos en warmup)
            atascados = []
            for par, ts in snapshot.items():
                if ahora - ts > WATCHDOG_PAR_STUCK_S:
                    atascados.append((par, int(ahora - ts)))
            if not atascados:
                continue
            # DEBOUNCE: si ya forzamos reconexión hace menos de
            # WATCHDOG_DEBOUNCE_S, no volver a forzar — le damos tiempo
            # al bot a recuperarse de la reconexión anterior.
            if ahora - _ultima_reconexion_forzada_por_watchdog < WATCHDOG_DEBOUNCE_S:
                continue
            _ultima_reconexion_forzada_por_watchdog = ahora
            # Marcar en estado y disparar reconexión
            with _lock:
                pares_estado = dict(estado.get("pares_estado") or {})
                for par, secs in atascados:
                    pares_estado[par] = {"senal": f"⏰ ATASCADO {secs}s"}
                estado["pares_estado"] = pares_estado
            for par, secs in atascados:
                log(f"[WATCHDOG] {par} sin avanzar {secs}s — forzando reconexión.")
            _forzar_reconexion(motivo=f"watchdog: {','.join(p for p,_ in atascados)} atascados")
        except Exception as e:
            # El watchdog NUNCA debe morir — si revienta, lo logueamos y sigue.
            try:
                log(f"[WATCHDOG] excepción (ignorada): {e}")
            except Exception:
                pass

# =============================================================
# HELPERS — candlestick utilities
# =============================================================

def is_bullish(c):
    return c["close"] > c["open"]

def is_bearish(c):
    return c["close"] < c["open"]

def body_size(c):
    return abs(c["close"] - c["open"])

def candle_range(c):
    return c["max"] - c["min"]


def fuerza_vela(c):
    """
    Fuerza de una vela individual: qué proporción de su rango total
    (max-min) corresponde a cuerpo real (close-open) en vez de mechas.
    0.0 = pura indecisión (mechas largas, cuerpo chico o nulo)
    1.0 = vela "perfecta", sin mechas, todo el movimiento fue decidido.
    """
    rango = candle_range(c)
    if rango <= 0:
        return 0.0
    return body_size(c) / rango


def calcular_micro_tendencia(velas, periodo=30, umbral_fuerza=0.5, umbral_dominancia=0.6):
    """
    Mide cuál dirección DOMINA en las últimas 'periodo' velas, usando no
    solo la dirección de cada vela (alcista/bajista) sino su FUERZA —
    para no contar como "tendencia" velas indecisas que cierran de un
    lado por pura casualidad.

    Una vela cuenta para una dirección solo si:
      - cierra en esa dirección (close > open para alcista, etc.)
      - Y su fuerza (cuerpo/rango) supera 'umbral_fuerza'

    Si el % de velas "fuertes" en una dirección, sobre el total de la
    ventana, supera 'umbral_dominancia', esa dirección domina la
    micro-tendencia reciente.

    Devuelve: ('ALCISTA' | 'BAJISTA' | None, proporcion_dominante)
      None si ninguna dirección alcanza el umbral de dominancia
      (mercado mixto/sin micro-tendencia clara — no bloquea nada).
    """
    if len(velas) < periodo:
        return None, 0.0

    ventana = velas[-periodo:]
    fuertes_alcistas = 0
    fuertes_bajistas = 0

    for c in ventana:
        f = fuerza_vela(c)
        if f < umbral_fuerza:
            continue
        if is_bullish(c):
            fuertes_alcistas += 1
        elif is_bearish(c):
            fuertes_bajistas += 1

    prop_alcista = fuertes_alcistas / periodo
    prop_bajista = fuertes_bajistas / periodo

    if prop_alcista >= umbral_dominancia:
        return "ALCISTA", prop_alcista
    if prop_bajista >= umbral_dominancia:
        return "BAJISTA", prop_bajista
    return None, max(prop_alcista, prop_bajista)


def detectar_vela_institucional(velas, periodo=20, multiplicador=3.5):
    """
    Detecta una vela "institucional"/anómala: una vela cuyo rango
    (max-min) es muchísimo más grande que el rango promedio reciente.
    Suele aparecer por una noticia importante, apertura/cierre de
    sesión, o un movimiento abrupto poco natural en OTC.

    Compara la ÚLTIMA vela CERRADA (velas[-1], que ya terminó) contra el
    promedio de rango de las 'periodo' velas anteriores a ella (sin
    incluirla, para no inflar el propio promedio con la vela atípica).

    Devuelve (es_institucional: bool, rango_vela: float, rango_prom: float)
    """
    if len(velas) < periodo + 1:
        return False, 0.0, 0.0

    vela_actual  = velas[-1]
    muestra_prev = velas[-(periodo + 1):-1]   # las 'periodo' anteriores, sin la actual

    rango_vela = candle_range(vela_actual)
    rango_prom = sum(candle_range(v) for v in muestra_prev) / len(muestra_prev)

    if rango_prom <= 0:
        return False, rango_vela, rango_prom

    es_institucional = rango_vela > rango_prom * multiplicador
    return es_institucional, rango_vela, rango_prom


def calcular_adx(velas, periodo=14):
    """
    ADX (Average Directional Index) de Wilder — mide la FUERZA de la
    tendencia (0-100), sin importar la dirección. No sustituye al MACD
    ni a la SMA34 (que sí dan dirección): es un filtro de calidad que
    distingue mercado con tendencia real de mercado lateral/ruidoso.

    Valores orientativos:
      < 20      → sin tendencia clara / lateral (zona de mayor riesgo
                  de falsas señales por cruces de MACD que son solo ruido)
      20-25     → tendencia débil / incipiente
      > 25      → tendencia con fuerza suficiente para confiar en ella

    Devuelve None si no hay suficientes velas para calcularlo todavía.
    """
    n = len(velas)
    # Wilder necesita 'periodo' valores de TR/DM + 'periodo' de suavizado
    # adicional para que el ADX (que es a su vez un promedio de DX) sea
    # estable, así que se requieren ~2*periodo velas como mínimo.
    if n < periodo * 2 + 1:
        return None

    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, n):
        actual, previa = velas[i], velas[i - 1]
        alto, bajo, cierre_prev = actual["max"], actual["min"], previa["close"]

        tr = max(alto - bajo, abs(alto - cierre_prev), abs(bajo - cierre_prev))
        tr_list.append(tr)

        up_move   = actual["max"] - previa["max"]
        down_move = previa["min"] - actual["min"]
        plus_dm  = up_move   if (up_move > down_move and up_move > 0)   else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    # Suavizado de Wilder (equivalente a una EMA con alpha = 1/periodo)
    def _wilder_smooth(valores, periodo):
        suavizado = [sum(valores[:periodo])]
        for v in valores[periodo:]:
            suavizado.append(suavizado[-1] - suavizado[-1] / periodo + v)
        return suavizado

    tr_suav    = _wilder_smooth(tr_list, periodo)
    plus_suav  = _wilder_smooth(plus_dm_list, periodo)
    minus_suav = _wilder_smooth(minus_dm_list, periodo)

    dx_list = []
    for tr_s, plus_s, minus_s in zip(tr_suav, plus_suav, minus_suav):
        if tr_s == 0:
            dx_list.append(0.0)
            continue
        plus_di  = 100 * (plus_s / tr_s)
        minus_di = 100 * (minus_s / tr_s)
        suma = plus_di + minus_di
        dx = 100 * (abs(plus_di - minus_di) / suma) if suma > 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < periodo:
        return None

    # ADX = promedio de Wilder sobre los valores de DX
    adx = sum(dx_list[:periodo]) / periodo
    for dx in dx_list[periodo:]:
        adx = (adx * (periodo - 1) + dx) / periodo

    return adx


# =============================================================
# ESTRATEGIA: SIGNAL PRO V1 (MACD simplificado, según script Lua)
#             + filtro de tendencia EMA 50
# =============================================================
#
# Señal (idéntica al script Lua "SIGNAL PRO V1"):
#   línea MACD    = SMA(precio, 1)  - SMA(precio, 34)
#   línea señal   = WMA(línea MACD, 5)
#   CALL = cruce de línea MACD hacia ARRIBA de la línea señal
#   SELL = cruce de línea MACD hacia ABAJO de la línea señal
#
# Filtro de tendencia (nuevo, no estaba en el Lua):
#   EMA 50 — precio por ENCIMA de la EMA50 → solo se permiten CALL
#            precio por DEBAJO de la EMA50 → solo se permiten SELL
#
# Timing (se mantiene igual que antes, sin cambios):
#   Vela 1 = se detecta el cruce MACD/señal     → NO entra, marca espera
#   Vela 2 = vela de confirmación/espera         → NO entra
#   Vela 3 = vela de ejecución                   → entra al segundo 00
#

MACD_FAST    = 1
MACD_SLOW    = 34
MACD_SIGNAL  = 5
EMA_TREND    = 50


def _sma(cierres, periodo):
    if len(cierres) < periodo:
        return None
    return sum(cierres[-periodo:]) / periodo


def _wma(serie, periodo):
    if len(serie) < periodo:
        return None
    ventana = serie[-periodo:]
    pesos   = list(range(1, periodo + 1))
    return sum(v * p for v, p in zip(ventana, pesos)) / sum(pesos)


def calcular_macd_serie(velas):
    if len(velas) < MACD_SLOW + MACD_SIGNAL:
        return [], []
    cierres = [v["close"] for v in velas]
    macd_series = []
    for i in range(MACD_SLOW - 1, len(cierres)):
        sma_fast = cierres[i] if MACD_FAST == 1 else sum(cierres[i - MACD_FAST + 1:i + 1]) / MACD_FAST
        sma_slow = sum(cierres[i - MACD_SLOW + 1:i + 1]) / MACD_SLOW
        macd_series.append(sma_fast - sma_slow)
    signal_series = []
    for i in range(MACD_SIGNAL - 1, len(macd_series)):
        wma = _wma(macd_series[i - MACD_SIGNAL + 1:i + 1], MACD_SIGNAL)
        signal_series.append(wma)
    return macd_series, signal_series


def detectar_cruce_macd(velas):
    macd_series, signal_series = calcular_macd_serie(velas)
    if len(macd_series) < 2 or len(signal_series) < 2:
        return None
    m_actual   = macd_series[-1]
    m_anterior = macd_series[-2]
    s_actual   = signal_series[-1]
    s_anterior = signal_series[-2]
    if m_anterior < s_anterior and m_actual > s_actual:
        return "CALL"
    if m_anterior > s_anterior and m_actual < s_actual:
        return "SELL"
    return None


def tendencia_ema50(velas):
    """Devuelve 'ALCISTA', 'BAJISTA' o None (datos insuficientes)."""
    if len(velas) < EMA_TREND:
        return None
    cierres = [v["close"] for v in velas]
    ema50 = calcular_ema(cierres, EMA_TREND)
    if ema50 is None:
        return None
    precio_actual = cierres[-1]
    return "ALCISTA" if precio_actual > ema50 else "BAJISTA"


# =============================================================
# ESTRATEGIA: BANDAS DE BOLLINGER + CCI  (reversión a la media)
#
# Portada desde la versión v31 (78W/60L ~ 56.5%). Reglas:
#   - SEÑAL PUT (SELL): la vela toca/casi-toca la banda superior de
#     Bollinger Y el CCI(14) viene de sobrecompra (+90) cruzando/girando
#     hacia abajo.
#   - SEÑAL CALL (BUY): la vela toca/casi-toca la banda inferior Y el
#     CCI(14) viene de sobreventa (-90) cruzando/girando hacia arriba.
#   - Sistema de puntos: base 75 + confirmadores; se exige >= 80.
#   - Entrada en la vela SIGUIENTE a la señal (el loop llama a
#     esperar_apertura_vela(), timing ya corregido).
# =============================================================

BB_PERIODO         = 20
BB_DESV            = 1.7          # desviaciones estándar de las bandas
CCI_PERIODO        = 14
CCI_SOBRECOMPRA    = 90
CCI_SOBREVENTA     = -90
CCI_LOOKBACK_CRUCE = 3           # velas hacia atrás para buscar cruce de CCI
TOQUE_TOLERANCIA   = 0.35        # 0.35 = acepta "casi-toques" (65% del camino)
PUNTOS_MINIMOS     = 80          # puntuación mínima para operar


def calcular_bollinger(velas, idx):
    """Bandas de Bollinger hasta idx (inclusive). idx puede ser negativo.
    Devuelve (banda_sup, banda_media, banda_inf) o (None, None, None)."""
    if idx < 0:
        idx = len(velas) + idx
    if idx < BB_PERIODO - 1:
        return None, None, None
    ventana = velas[idx - BB_PERIODO + 1 : idx + 1]
    cierres = [v["close"] for v in ventana]
    sma = sum(cierres) / BB_PERIODO
    varianza = sum((c - sma) ** 2 for c in cierres) / BB_PERIODO
    std = varianza ** 0.5
    return sma + (BB_DESV * std), sma, sma - (BB_DESV * std)


def calcular_cci(velas, idx):
    """CCI(14) hasta idx (inclusive). idx puede ser negativo. float o None."""
    if idx < 0:
        idx = len(velas) + idx
    if idx < CCI_PERIODO - 1:
        return None
    ventana = velas[idx - CCI_PERIODO + 1 : idx + 1]
    tp = [(v["max"] + v["min"] + v["close"]) / 3 for v in ventana]
    sma_tp = sum(tp) / CCI_PERIODO
    desviacion_media = sum(abs(t - sma_tp) for t in tp) / CCI_PERIODO
    if desviacion_media == 0:
        return 0.0
    return (tp[-1] - sma_tp) / (0.015 * desviacion_media)


def toca_banda_superior(vela, banda_sup, banda_media, tolerancia=TOQUE_TOLERANCIA):
    ancho = banda_sup - banda_media
    umbral = banda_sup - (ancho * tolerancia)
    return vela["max"] >= umbral


def toca_banda_inferior(vela, banda_inf, banda_media, tolerancia=TOQUE_TOLERANCIA):
    ancho = banda_media - banda_inf
    umbral = banda_inf + (ancho * tolerancia)
    return vela["min"] <= umbral


def detectar_bollinger_cci(velas):
    """Detecta señal Bollinger + CCI (reversión). Devuelve dict o None."""
    if len(velas) < 30:
        return None

    vela_actual = velas[-1]

    # Bandas con la vela cerrada anterior (-2) para no mirar dentro de la actual
    banda_sup, banda_media, banda_inf = calcular_bollinger(velas, -2)
    if banda_sup is None:
        return None

    cci_act = calcular_cci(velas, -1)
    cci_ant = calcular_cci(velas, -2)
    if cci_act is None or cci_ant is None:
        return None

    cci_serie = []
    for k in range(CCI_LOOKBACK_CRUCE + 1, 0, -1):
        try:
            v = calcular_cci(velas, -k)
            if v is not None:
                cci_serie.append(v)
        except Exception:
            pass

    def cruce_descendente(serie, nivel):
        for i in range(1, len(serie)):
            if serie[i-1] > nivel and serie[i] < nivel:
                return True
        return False

    def cruce_ascendente(serie, nivel):
        for i in range(1, len(serie)):
            if serie[i-1] < nivel and serie[i] > nivel:
                return True
        return False

    # PUT: banda superior + sobrecompra girando/cruzando hacia abajo
    if toca_banda_superior(vela_actual, banda_sup, banda_media):
        cruce_sobrecompra = cruce_descendente(cci_serie, CCI_SOBRECOMPRA)
        girando_abajo = (cci_ant > CCI_SOBRECOMPRA and cci_act < cci_ant)
        if cruce_sobrecompra or girando_abajo:
            cierre_firme = vela_actual["close"] > banda_sup
            puntos = 75
            if cruce_sobrecompra:       puntos += 10
            if cierre_firme:            puntos += 10
            if abs(cci_act) > 150:      puntos += 5
            if is_bearish(vela_actual): puntos += 10
            return {"senal": "PUT", "patron": "BOLLINGER_CCI_PUT",
                    "cierre_firme": cierre_firme, "cci": round(cci_act, 1),
                    "banda": round(banda_sup, 5), "puntos": puntos}

    # CALL: banda inferior + sobreventa girando/cruzando hacia arriba
    if toca_banda_inferior(vela_actual, banda_inf, banda_media):
        cruce_sobreventa = cruce_ascendente(cci_serie, CCI_SOBREVENTA)
        girando_arriba = (cci_ant < CCI_SOBREVENTA and cci_act > cci_ant)
        if cruce_sobreventa or girando_arriba:
            cierre_firme = vela_actual["close"] < banda_inf
            puntos = 75
            if cruce_sobreventa:        puntos += 10
            if cierre_firme:            puntos += 10
            if abs(cci_act) > 150:      puntos += 5
            if is_bullish(vela_actual): puntos += 10
            return {"senal": "CALL", "patron": "BOLLINGER_CCI_CALL",
                    "cierre_firme": cierre_firme, "cci": round(cci_act, 1),
                    "banda": round(banda_inf, 5), "puntos": puntos}

    return None


# =============================================================
# ESTRATEGIA: INDECISIÓN, FUERZA y CONTINUIDAD (IFC)
#
# Secuencia de 3 velas:
#   1) INDECISIÓN: vela con mecha superior E inferior y cuerpo pequeño
#      (tipo doji / spinning top). Marca duda en el mercado.
#   2) FUERZA: vela decidida (cuerpo >= 70% del rango) que CUBRE la vela
#      de indecisión (cierra más allá de todo su rango). Su dirección
#      define la operación — "seguir el 100% del movimiento de la vela
#      que acaba de cerrar".
#   3) CONTINUIDAD: se ENTRA en la apertura de la vela siguiente, a favor
#      de la vela de fuerza (el loop llama a esperar_apertura_vela()).
#
# Filtro de dirección por EMAs (rápida vs lenta): solo COMPRAS si es
# alcista y VENTAS si es bajista — nunca contra la tendencia.
# Opcional: exigir que indecisión y fuerza sean del mismo color.
# =============================================================

def _mecha_superior(c):
    return c["max"] - max(c["open"], c["close"])

def _mecha_inferior(c):
    return min(c["open"], c["close"]) - c["min"]

def _cuerpo_ratio(c):
    r = candle_range(c)
    if r <= 0:
        return 0.0
    return body_size(c) / r

def es_vela_indecision(c, cuerpo_max, rango_max=None):
    """Indecisión = vela que NO tomó una fuerza dominante:
      - tiene mecha arriba y abajo (cualquier tamaño, pero ambas),
      - cuerpo que no domina el rango (<= cuerpo_max),
      - y que no sea demasiado grande (rango <= rango_max, si se pasa)."""
    r = candle_range(c)
    if r <= 0:
        return False
    if _mecha_superior(c) <= 0 or _mecha_inferior(c) <= 0:
        return False                     # deben estar AMBAS mechas
    if _cuerpo_ratio(c) > cuerpo_max:
        return False                     # cuerpo dominante -> no es indecisión
    if rango_max is not None and r > rango_max:
        return False                     # "que no sea tan grande"
    return True

def tendencia_emas(velas):
    """Dirección por dos EMAs (rápida vs lenta). ALCISTA/BAJISTA/NEUTRAL/None."""
    cierres = [v["close"] for v in velas]
    er = calcular_ema(cierres, config.get("ifc_ema_rapida", 9))
    el = calcular_ema(cierres, config.get("ifc_ema_lenta", 21))
    if er is None or el is None:
        return None
    if er > el:
        return "ALCISTA"
    if er < el:
        return "BAJISTA"
    return "NEUTRAL"

def detectar_ifc(velas):
    """Patrón Indecisión + Fuerza + Continuidad.
    velas[-2] = indecisión, velas[-1] = fuerza (recién cerrada).
    La entrada real es en la vela siguiente. Devuelve dict o None."""
    if len(velas) < 3:
        return None

    indec  = velas[-2]
    fuerza = velas[-1]

    cuerpo_max = config.get("ifc_indecision_cuerpo_max", 0.50)
    cuerpo_min = config.get("ifc_fuerza_cuerpo_min", 0.70)
    fuerza_cuerpo_max = config.get("ifc_fuerza_cuerpo_max", 0.90)

    # Rango promedio de las velas previas (para el filtro "que no sea tan
    # grande"): la vela de indecisión no debe ser un candelón de volatilidad.
    ventana = velas[-22:-2]   # 20 velas antes de la indecisión
    rango_max = None
    if len(ventana) >= 5:
        rango_prom = sum(candle_range(v) for v in ventana) / len(ventana)
        rango_max = rango_prom * config.get("ifc_indecision_rango_max_x", 2.0)

    # 1) vela de indecisión (no dominante, dos mechas, no demasiado grande)
    if not es_vela_indecision(indec, cuerpo_max, rango_max):
        return None

    # 2) vela de fuerza (cuerpo grande, decidida) — con TOPE MÁXIMO:
    #    si el cuerpo es > 90% del rango, la vela es "demasiado perfecta"
    #    (casi sin mechas, típica de noticias o gap) y se descarta. Solo
    #    se aceptan velas de fuerza con cuerpo entre cuerpo_min (70%) y
    #    fuerza_cuerpo_max (90%).
    ratio_fuerza = _cuerpo_ratio(fuerza)
    if ratio_fuerza < cuerpo_min:
        return None
    if ratio_fuerza > fuerza_cuerpo_max:
        return None

    # 3) la fuerza CUBRE la indecisión: cierra más allá de TODO su rango
    direccion = None
    if is_bullish(fuerza) and fuerza["close"] >= indec["max"]:
        direccion = "CALL"
    elif is_bearish(fuerza) and fuerza["close"] <= indec["min"]:
        direccion = "PUT"
    if direccion is None:
        return None

    # 4) (opcional) mismo color: indecisión y fuerza en el mismo sentido
    mismo_color = (is_bullish(indec) and is_bullish(fuerza)) or \
                  (is_bearish(indec) and is_bearish(fuerza))
    if config.get("ifc_mismo_color", False) and not mismo_color:
        return None

    return {"dir": direccion, "mismo_color": mismo_color,
            "cuerpo_fuerza": round(_cuerpo_ratio(fuerza) * 100)}


def _obtener_modo_par(par):
    """Modo IFC efectivo del par, con esta prioridad:
      1) MANUAL del usuario (config['ifc_modo_por_par'][par]) — mayor prioridad
      2) AUTO-CALIBRACIÓN (_modo_por_par[par], asignado por calibrar_pares)
      3) GLOBAL (config['ifc_modo'])
    Así el usuario siempre gana: si asigna manualmente C o R a un par,
    ninguna calibración automática lo va a sobrescribir."""
    if par:
        manual = (config.get("ifc_modo_por_par") or {}).get(par)
        if manual in ("continuidad", "reversion"):
            return manual
        auto = _modo_por_par.get(par)
        if auto in ("continuidad", "reversion"):
            return auto
    return config.get("ifc_modo", "continuidad")


def _analizar_ifc(velas, estado_senal=None, par=None):
    """Estrategia Indecisión-Fuerza-Continuidad (IFC).
    Devuelve (BUY|SELL|NONE, descripción). 'estado_senal' se ignora; se
    mantiene por compatibilidad con loop_par y el warmup. La entrada real
    ocurre en la apertura de la vela siguiente (la de continuidad)."""
    minimo = max(3, config.get("ifc_ema_lenta", 21) + 2)
    if len(velas) < minimo:
        return "NONE", f"Pocas velas (mín. {minimo})"

    det = detectar_ifc(velas)
    if det is None:
        return "NONE", "Sin patrón IFC (falta indecisión + fuerza que la cubra)"

    dir_fuerza = det["dir"]   # dirección de la vela de FUERZA (CALL/PUT)

    # Filtro de tendencia por EMAs: exigimos que la FUERZA vaya a favor de
    # la tendencia (en ambos modos). En continuidad operamos a favor de la
    # fuerza; en reversión apostamos lo contrario a esa fuerza.
    if config.get("ifc_usar_ema", True):
        tendencia = tendencia_emas(velas)
        if tendencia is None:
            return "NONE", "EMAs sin datos suficientes"
        if dir_fuerza == "CALL" and tendencia != "ALCISTA":
            return "NONE", f"Descartada: fuerza alcista pero tendencia {tendencia}"
        if dir_fuerza == "PUT" and tendencia != "BAJISTA":
            return "NONE", f"Descartada: fuerza bajista pero tendencia {tendencia}"
        tend_txt = f" | EMA:{tendencia}"
    else:
        tend_txt = ""

    # Modo propio del par: elección MANUAL del usuario > auto-calibración
    # > global. Ver _obtener_modo_par().
    modo = _obtener_modo_par(par)
    if modo == "reversion":
        direccion = "PUT" if dir_fuerza == "CALL" else "CALL"   # apostar lo contrario
        modo_txt = " REVERSIÓN"
    else:
        direccion = dir_fuerza
        modo_txt = ""

    dir_txt   = "COMPRA" if direccion == "CALL" else "VENTA"
    color_txt = " | mismo color" if det["mismo_color"] else ""
    return (("BUY" if direccion == "CALL" else "SELL"),
            f"{dir_txt} | IFC{modo_txt} | cuerpo fuerza:{det['cuerpo_fuerza']}%{color_txt}{tend_txt} | entrada vela continuidad")


def _ifcpro_ruptura(velas_hasta):
    """¿La vela recién cerrada (velas_hasta[-1]) es una FUERZA que rompe el
    nivel marcado por la indecisión previa? Reutiliza detectar_ifc para el
    patrón indecisión+fuerza y añade la condición de ruptura del nivel.
    Devuelve dict {dir, nivel, atr} o None."""
    det = detectar_ifc(velas_hasta)
    if det is None:
        return None
    if config.get("ifcpro_usar_ema", True):
        tend = tendencia_emas(velas_hasta)
        if tend is None:
            return None
        if det["dir"] == "CALL" and tend != "ALCISTA":
            return None
        if det["dir"] == "PUT" and tend != "BAJISTA":
            return None
    indec  = velas_hasta[-2]
    fuerza = velas_hasta[-1]
    atr = _atr_promedio(velas_hasta, 14)
    if not atr or atr <= 0:
        return None
    margen = atr * float(config.get("ifcpro_ruptura_min_atr", 0.05))
    # El nivel es la mecha de la indecisión que la fuerza atraviesa.
    if det["dir"] == "CALL":
        nivel = indec["max"]                       # resistencia dinámica rota al alza
        if fuerza["close"] < nivel + margen:
            return None
    else:
        nivel = indec["min"]                       # soporte dinámico roto a la baja
        if fuerza["close"] > nivel - margen:
            return None
    return {"dir": det["dir"], "nivel": nivel, "atr": atr}


def detectar_ifcpro(velas):
    """Detecta una entrada IFC-Pro EN LA VELA ACTUAL (velas[-1]).
    Busca hacia atrás una ruptura reciente (dentro de ifcpro_espera_max
    velas) cuya zona de activación esté siendo tocada AHORA por el precio,
    con el retroceso ya cumplido. Devuelve dict o None.
    - La entrada real es en la apertura de la vela siguiente (la maneja el
      loop / el backtest), a favor de la dirección de la fuerza."""
    espera_max = int(config.get("ifcpro_espera_max", 8))
    if len(velas) < espera_max + 5:
        return None
    actual = velas[-1]
    # Recorremos posibles velas de FUERZA en el pasado reciente: la fuerza
    # está en el índice -k (k desde 2 hasta espera_max+1). Entre la fuerza
    # y la vela actual debe haber transcurrido el retroceso.
    for k in range(2, espera_max + 2):
        idx_fuerza = len(velas) - k
        if idx_fuerza < 25:
            break
        rup = _ifcpro_ruptura(velas[:idx_fuerza + 1])
        if rup is None:
            continue
        nivel = rup["nivel"]; atr = rup["atr"]; direccion = rup["dir"]
        cerca = atr * float(config.get("ifcpro_pullback_atr", 0.15))
        velas_entre = velas[idx_fuerza + 1:]        # velas desde la fuerza hasta la actual
        if not velas_entre:
            continue
        # El precio NO debe haber vuelto a cruzar el nivel en contra antes
        # del toque (si lo cruzó, la ruptura ya se invalidó).
        if direccion == "CALL":
            # tras romper hacia arriba, el retroceso baja a tocar el nivel.
            invalidada = any(v["close"] < nivel - atr * 0.5 for v in velas_entre[:-1])
            if invalidada:
                continue
            toca_ahora = (actual["min"] <= nivel + cerca)
            # confirmamos que la vela actual bajó a la zona pero no se
            # desplomó por debajo (el nivel "aguanta"): cierre >= nivel - cerca
            if toca_ahora and actual["close"] >= nivel - cerca:
                return {"dir": direccion, "nivel": round(nivel, 5),
                        "espera": len(velas_entre), "calidad": 80}
        else:
            invalidada = any(v["close"] > nivel + atr * 0.5 for v in velas_entre[:-1])
            if invalidada:
                continue
            toca_ahora = actual["max"] >= nivel - cerca
            if toca_ahora and actual["close"] <= nivel + cerca:
                return {"dir": direccion, "nivel": round(nivel, 5),
                        "espera": len(velas_entre), "calidad": 80}
    return None


def _analizar_ifcpro(velas):
    """Estrategia IFC-Pro. Entra a favor de la fuerza cuando el precio
    retrocede a la zona de activación del nivel roto. Entrada en la
    apertura de la vela siguiente."""
    espera_max = int(config.get("ifcpro_espera_max", 8))
    if len(velas) < espera_max + 6:
        return "NONE", f"Pocas velas (mín. {espera_max + 6})"
    det = detectar_ifcpro(velas)
    if det is None:
        return "NONE", "Sin señal IFC-Pro (sin ruptura reciente con retroceso a la zona)"
    direccion = det["dir"]
    dir_txt = "COMPRA" if direccion == "CALL" else "VENTA"
    return (("BUY" if direccion == "CALL" else "SELL"),
            f"{dir_txt} | IFC-PRO | zona activación {det['nivel']} | retroceso {det['espera']} velas | entrada vela siguiente")


# =============================================================
# ESTRATEGIA: FACUNDO — Price action sobre líneas importantes
#
# Basada en el video "Táctica 99% EFECTIVA para GANAR en OTC" de
# Facundo Contreras. Price action puro sobre soportes y resistencias
# trazados automáticamente a partir de máximos/mínimos recientes.
#
# Tres variantes de entrada:
#   1) RECHAZO:    vela[-2] se acerca a un S/R y deja mecha de rechazo
#                  (mecha >= 30% del rango); vela[-1] confirma con cuerpo
#                  decisivo en la dirección del rechazo. Entrada a favor
#                  del rechazo (PUT en resistencia, CALL en soporte).
#   2) RUPTURA:    vela[-2] cierra más allá del S/R con cuerpo >= 60%
#                  y margen >= N pips; vela[-1] confirma con cuerpo en
#                  la misma dirección. Entrada a favor del rompimiento.
#   3) LATERAL:    identifica rango en las últimas N velas (techo/piso);
#                  si vela[-2] toca un extremo y vela[-1] confirma el
#                  rebote, entra hacia el centro del rango.
#
# Filtro opcional por EMAs (no operar contra tendencia fuerte).
# Entrada real en la apertura de la vela siguiente (la maneja el loop).
# =============================================================

def _facundo_sr_niveles(velas_previas):
    """Niveles S/R para la estrategia Facundo — VERSIÓN CORREGIDA.

    Diferencias clave contra el detector genérico (detectar_sr):
      1) Un nivel solo cuenta toques de VELAS DISTINTAS y SEPARADAS. Antes,
         el máximo y el mínimo de UNA MISMA vela pequeña contaban como 2
         "toques" (una vela de 1 pip creaba sola un 'nivel importante').
         Ahora una vela aporta como máximo 1 toque, y toques de velas
         consecutivas en la misma zona (congestión) cuentan como UNO:
         para sumar un toque nuevo el precio debe haberse ido y vuelto
         (>= facundo_sr_separacion_velas velas desde el toque anterior).
      2) El lookback de 'facundo_sr_lookback' se aplica AQUÍ directamente.
         Antes se pasaba un subset a detectar_sr(), que por dentro volvía
         a recortar con config['sr_lookback'] (50) — el valor 80 de la
         config de Facundo nunca tenía efecto real.
      3) Devuelve [(precio, toques)] para que el caller pueda exigir un
         mínimo de toques PREVIOS (regla del 3er toque).

    'velas_previas' deben ser las velas ANTERIORES a la vela de señal
    (sin incluir la de rechazo/ruptura ni la de confirmación), para que
    el toque de la vela de señal sea genuinamente un toque NUEVO de un
    nivel preexistente."""
    lookback = int(config.get("facundo_sr_lookback", 80))
    zona = float(config.get("facundo_sr_zone_pips", 0.0003))
    separacion = int(config.get("facundo_sr_separacion_velas", 3))
    subset = velas_previas[-lookback:] if len(velas_previas) >= lookback else velas_previas
    niveles = []   # [{"price": float, "toques": [idx de toques aceptados]}]
    for idx, v in enumerate(subset):
        for precio in (v["max"], v["min"]):
            destino = None
            for lvl in niveles:
                if abs(lvl["price"] - precio) <= zona:
                    destino = lvl
                    break
            if destino is None:
                niveles.append({"price": precio, "toques": [idx]})
                continue
            ultimo = destino["toques"][-1]
            if idx == ultimo:
                continue   # la misma vela (max y min en la zona) = 1 solo toque
            # SEPARACIÓN entre toques: una congestión de velas consecutivas
            # en la misma zona NO es un nivel re-visitado, es el precio
            # parado. Solo cuenta como toque nuevo si el precio se fue y
            # volvió: al menos 'separacion' velas desde el toque anterior.
            if idx - ultimo < separacion:
                continue
            n = len(destino["toques"])
            destino["price"] = (destino["price"] * n + precio) / (n + 1)
            destino["toques"].append(idx)
    return [(lvl["price"], len(lvl["toques"])) for lvl in niveles]


def _facundo_toques_ok(t):
    """Regla completa de toques previos del usuario: mínimo
    facundo_sr_toques_previos (default 2 → entrada en el 3er toque) y
    máximo facundo_sr_toques_max_previos (default 3 → hasta el 4to
    toque; un nivel más golpeado se considera gastado). 0 = sin tope."""
    t_min = int(config.get("facundo_sr_toques_previos", 2))
    t_max = int(config.get("facundo_sr_toques_max_previos", 0) or 0)
    if t < t_min:
        return False
    if t_max > 0 and t > t_max:
        return False
    return True


def _facundo_niveles_validos(velas_previas):
    """Niveles que cumplen la regla del usuario: la entrada debe ser como
    mínimo el 3er toque del nivel. Es decir, el nivel necesita
    'facundo_sr_toques_previos' toques (default 2) de velas DISTINTAS
    ANTES de la vela de señal — el toque de la vela de señal es el 3ro.
    Devuelve lista de precios."""
    return [p for (p, t) in _facundo_sr_niveles(velas_previas) if _facundo_toques_ok(t)]


def _facundo_filtro_ema(direccion, velas):
    """Filtro de tendencia: si la EMA rápida/lenta está claramente en
    contra de 'direccion', descarta. IMPORTANTE: desde el arreglo del
    orden filtro/inversión, este filtro se aplica en _facundo_decidir()
    sobre la dirección FINAL de entrada (después de facundo_invertir),
    que es la que de verdad se ejecuta — no sobre la dirección original
    del detector. Devuelve True si la señal pasa, False si se descarta."""
    if not config.get("facundo_usar_ema", True):
        return True
    tend = tendencia_emas(velas)
    if tend is None or tend == "NEUTRAL":
        return True
    if direccion == "CALL" and tend == "BAJISTA":
        return False
    if direccion == "PUT" and tend == "ALCISTA":
        return False
    return True


def detectar_facundo_rechazo(velas):
    """Detecta un RECHAZO en nivel importante (estilo Facundo Contreras).
    Secuencia en velas[-2] (rechazo) y velas[-1] (confirmación):
      - velas[-2] se acerca a un S/R VALIDADO (con al menos
        facundo_sr_toques_previos toques de velas distintas ANTES — la
        entrada es como mínimo el 3er toque del nivel) y deja MECHA de
        rechazo (>= facundo_mecha_min_ratio del rango). Mecha superior en
        resistencia → PUT; mecha inferior en soporte → CALL.
      - velas[-1] confirma con cuerpo decisivo (>= facundo_confirm_cuerpo_min)
        en la dirección del rechazo.
    Los niveles se calculan sobre velas[:-2] (previas a la señal), para
    que el toque del rechazo sea un toque NUEVO de un nivel preexistente.
    El filtro EMA ya NO se aplica aquí: lo aplica _facundo_decidir() sobre
    la dirección final de entrada (después de la inversión).
    Devuelve dict {dir, nivel, toques, tipo:"rechazo", cuerpo_conf} o None."""
    if len(velas) < 25:
        return None
    zona = float(config.get("facundo_sr_zone_pips", 0.0003))
    mecha_min = float(config.get("facundo_mecha_min_ratio", 0.30))
    confirm_min = float(config.get("facundo_confirm_cuerpo_min", 0.40))

    # Niveles construidos SIN la vela de rechazo ni la de confirmación.
    sr_levels = [(p, t) for (p, t) in _facundo_sr_niveles(velas[:-2])
                 if _facundo_toques_ok(t)]
    if not sr_levels:
        return None

    rechazo = velas[-2]
    confirm = velas[-1]
    rango_rech = candle_range(rechazo)
    rango_conf = candle_range(confirm)
    if rango_rech <= 0 or rango_conf <= 0:
        return None

    mecha_sup = _mecha_superior(rechazo)
    mecha_inf = _mecha_inferior(rechazo)

    # AJUSTE v9.1.2: recorrer TODOS los niveles válidos y seleccionar el de
    # MAYOR cantidad de toques previos. Antes se hacía break en el primer
    # nivel que coincidía con la mecha, perdiendo la oportunidad de operar
    # niveles más fuertes (4to/5to toque) cuando existían en la misma zona.
    # Esto implementa la regla del usuario: "tomar como referencia el nivel
    # que más toques tenga".
    direccion = None
    nivel_tocado = None
    toques_nivel = 0
    candidatos = []
    # Resistencia: mecha superior larga cerca de un nivel probado → PUT
    if mecha_sup > 0 and mecha_sup / rango_rech >= mecha_min:
        extremo = rechazo["max"]
        for lvl, toques in sr_levels:
            if abs(extremo - lvl) <= zona:
                candidatos.append(("PUT", lvl, toques))
    # Soporte: mecha inferior larga cerca de un nivel probado → CALL
    if not candidatos and mecha_inf > 0 and mecha_inf / rango_rech >= mecha_min:
        extremo = rechazo["min"]
        for lvl, toques in sr_levels:
            if abs(extremo - lvl) <= zona:
                candidatos.append(("CALL", lvl, toques))
    if not candidatos:
        return None
    # Elegir el nivel con MÁS toques previos (prioriza 4to sobre 3ro).
    # En caso de empate en toques, el primero encontrado (orden de sr_levels).
    direccion, nivel_tocado, toques_nivel = max(candidatos, key=lambda c: c[2])
    # AJUSTE v9.1.2: log de diagnóstico cuando hay múltiples niveles
    # candidatos y se eligió el de más toques. Solo se loguea si hubo
    # decisión (más de un candidato), para no llenar el log de ruido.
    if len(candidatos) > 1:
        try:
            log(f"[FACUNDO-RECHAZO] {len(candidatos)} niveles válidos cerca de la mecha. "
                f"Elegido: {direccion} @ {nivel_tocado:.5f} ({toques_nivel} toques prev). "
                f"Otros: " + ", ".join(f"{c[0]}@{c[1]:.5f}({c[2]})" for c in candidatos if c[2] != toques_nivel))
        except Exception:
            pass  # el log es diagnóstico, no debe romper la operativa

    # Confirmación: vela siguiente con cuerpo en la dirección del rechazo
    cuerpo_conf = _cuerpo_ratio(confirm)
    if cuerpo_conf < confirm_min:
        return None
    if direccion == "CALL" and not is_bullish(confirm):
        return None
    if direccion == "PUT" and not is_bearish(confirm):
        return None

    return {"dir": direccion, "nivel": round(nivel_tocado, 5),
            "toques": toques_nivel + 1,   # +1 = el toque de esta entrada
            "tipo": "rechazo", "cuerpo_conf": round(cuerpo_conf * 100)}


def detectar_facundo_rompimiento(velas):
    """Detecta un ROMPIMIENTO de S/R con confirmación (estilo Facundo).
    Secuencia en velas[-2] (ruptura) y velas[-1] (confirmación):
      - velas[-2] CIERRA más allá del S/R con cuerpo decisivo
        (>= facundo_romp_cuerpo_min del rango) y margen
        (>= facundo_romp_margen_pips más allá del nivel). La apertura
        debe estar del lado interno del nivel (que la vela realmente
        lo cruzó, no que nació afuera).
      - velas[-1] confirma con cuerpo en la misma dirección.
    Entrada a favor del rompimiento. Devuelve dict o None."""
    if len(velas) < 25:
        return None
    zona = float(config.get("facundo_sr_zone_pips", 0.0003))
    cuerpo_min = float(config.get("facundo_romp_cuerpo_min", 0.60))
    margen = float(config.get("facundo_romp_margen_pips", 0.0003))
    confirm_min = float(config.get("facundo_confirm_cuerpo_min", 0.40))

    # Un rompimiento solo significa algo si el nivel roto era REAL: se
    # exige el mismo mínimo de toques previos (velas distintas) que en el
    # rechazo. Los niveles se calculan sin la vela de ruptura ni la de
    # confirmación. El filtro EMA se aplica en _facundo_decidir().
    sr_levels = [(p, t) for (p, t) in _facundo_sr_niveles(velas[:-2])
                 if _facundo_toques_ok(t)]
    if not sr_levels:
        return None

    ruptura = velas[-2]
    confirm = velas[-1]
    if candle_range(ruptura) <= 0 or candle_range(confirm) <= 0:
        return None
    if _cuerpo_ratio(ruptura) < cuerpo_min:
        return None

    direccion = None
    nivel_roto = None
    toques_nivel = 0
    # AJUSTE v9.1.2: recorrer TODOS los niveles válidos y seleccionar el de
    # MAYOR toques previos. Igual que en detectar_facundo_rechazo, antes se
    # hacía break en el primer nivel roto encontrado.
    candidatos = []
    # Ruptura alcista: cierre por encima de la resistencia + margen,
    # y apertura del lado interno (<= nivel + zona) para confirmar cruce.
    if is_bullish(ruptura):
        for lvl, toques in sr_levels:
            if ruptura["close"] > lvl + margen and ruptura["open"] <= lvl + zona:
                candidatos.append(("CALL", lvl, toques))
    # Ruptura bajista: cierre por debajo del soporte + margen,
    # y apertura del lado interno (>= nivel - zona).
    if not candidatos and is_bearish(ruptura):
        for lvl, toques in sr_levels:
            if ruptura["close"] < lvl - margen and ruptura["open"] >= lvl - zona:
                candidatos.append(("PUT", lvl, toques))
    if not candidatos:
        return None
    # Elegir el nivel roto con MÁS toques previos (nivel más fuerte).
    direccion, nivel_roto, toques_nivel = max(candidatos, key=lambda c: c[2])
    # AJUSTE v9.1.2: log de diagnóstico (igual que en detectar_facundo_rechazo).
    if len(candidatos) > 1:
        try:
            log(f"[FACUNDO-ROMPIMIENTO] {len(candidatos)} niveles rotos. "
                f"Elegido: {direccion} @ {nivel_roto:.5f} ({toques_nivel} toques prev). "
                f"Otros: " + ", ".join(f"{c[0]}@{c[1]:.5f}({c[2]})" for c in candidatos if c[2] != toques_nivel))
        except Exception:
            pass

    cuerpo_conf = _cuerpo_ratio(confirm)
    if cuerpo_conf < confirm_min:
        return None
    if direccion == "CALL" and not is_bullish(confirm):
        return None
    if direccion == "PUT" and not is_bearish(confirm):
        return None

    return {"dir": direccion, "nivel": round(nivel_roto, 5),
            "toques": toques_nivel,
            "tipo": "rompimiento", "cuerpo_conf": round(cuerpo_conf * 100)}


def detectar_facundo_lateral(velas):
    """Detecta entrada en extremo de rango lateral (estilo Facundo).
    Identifica un rango en las últimas 'facundo_lateral_lookback' velas
    (excluyendo las dos últimas, que son las de operación): techo = max
    de los máximos, piso = min de los mínimos. Si el rango es razonable
    (no más ancho que facundo_lateral_max_amplitud_x veces la vela
    promedio) y velas[-2] toca un extremo con mecha de rechazo, y
    velas[-1] confirma moviéndose al centro del rango, entra a favor
    del rebote (PUT en el techo, CALL en el piso).
    Devuelve dict o None."""
    if len(velas) < 30:
        return None
    lookback = int(config.get("facundo_lateral_lookback", 30))
    tol = float(config.get("facundo_lateral_tolerancia_pips", 0.0005))
    max_amp_x = float(config.get("facundo_lateral_max_amplitud_x", 8))
    confirm_min = float(config.get("facundo_confirm_cuerpo_min", 0.40))

    ventana = velas[-lookback:-2] if len(velas) >= lookback + 2 else velas[:-2]
    if len(ventana) < 10:
        return None
    techo = max(v["max"] for v in ventana)
    piso = min(v["min"] for v in ventana)
    amplitud = techo - piso
    if amplitud <= 0:
        return None
    rango_prom = sum(candle_range(v) for v in ventana) / len(ventana)
    if rango_prom > 0 and amplitud > rango_prom * max_amp_x:
        return None  # rango demasiado ancho = no es lateralización real

    # Regla del 3er toque también para el rango: el techo/piso debe haber
    # sido tocado por al menos 'facundo_sr_toques_previos' velas DISTINTAS
    # Y SEPARADAS (el precio se fue y volvió) dentro de la ventana, antes
    # de la vela de señal. Así el extremo del rango es un nivel probado,
    # no el máximo casual de una congestión de velas consecutivas.
    separacion = int(config.get("facundo_sr_separacion_velas", 3))

    def _toques_separados(extremo_de, objetivo):
        toques, ultimo = 0, -10**9
        for i2, v2 in enumerate(ventana):
            if abs(extremo_de(v2) - objetivo) <= tol and i2 - ultimo >= separacion:
                toques += 1
                ultimo = i2
        return toques

    toques_techo = _toques_separados(lambda v2: v2["max"], techo)
    toques_piso  = _toques_separados(lambda v2: v2["min"], piso)

    rechazo = velas[-2]
    confirm = velas[-1]
    if candle_range(rechazo) <= 0 or candle_range(confirm) <= 0:
        return None

    direccion = None
    nivel = None
    toques_nivel = 0
    # Toca el techo (probado, dentro del rango de toques) con mecha superior → PUT
    if (abs(rechazo["max"] - techo) <= tol and _mecha_superior(rechazo) > 0
            and _facundo_toques_ok(toques_techo)):
        direccion = "PUT"
        nivel = techo
        toques_nivel = toques_techo
    # Toca el piso (probado, dentro del rango de toques) con mecha inferior → CALL
    elif (abs(rechazo["min"] - piso) <= tol and _mecha_inferior(rechazo) > 0
            and _facundo_toques_ok(toques_piso)):
        direccion = "CALL"
        nivel = piso
        toques_nivel = toques_piso
    if direccion is None:
        return None

    cuerpo_conf = _cuerpo_ratio(confirm)
    if cuerpo_conf < confirm_min:
        return None
    if direccion == "CALL" and not is_bullish(confirm):
        return None
    if direccion == "PUT" and not is_bearish(confirm):
        return None

    return {"dir": direccion, "nivel": round(nivel, 5),
            "toques": toques_nivel + 1,   # +1 = el toque de esta entrada
            "tipo": "lateral", "cuerpo_conf": round(cuerpo_conf * 100)}


def _facundo_decidir(velas, modo=None, invertir=None, usar_ema=None):
    """Núcleo de decisión de la estrategia Facundo — SIN efectos
    secundarios sobre la config global. El vivo (_analizar_facundo) y el
    backtest (_backtest_facundo) llaman a ESTA MISMA función, así que lo
    que mide el backtest es exactamente lo que opera el bot.

    Parámetros None → se leen de config (comportamiento en vivo). El
    backtest los pasa explícitos para comparar variantes sin tocar la
    config (antes el backtest mutaba config['facundo_modo'] y
    config['facundo_invertir'] mientras corría: si el bot estaba operando
    Facundo en vivo en ese momento, podía ejecutar entradas con la
    dirección OPUESTA a la configurada — race condition eliminada).

    ORDEN CORREGIDO filtro/inversión: el filtro EMA se aplica sobre la
    dirección FINAL de entrada (después de aplicar facundo_invertir).
    Antes se filtraba la dirección original del detector y LUEGO se
    invertía, con lo que el 100% de las entradas ejecutadas iban contra
    la tendencia EMA — el filtro protegía exactamente al revés.

    Devuelve (senal "BUY"/"SELL"/"NONE", det|None, razon)."""
    if len(velas) < 30:
        return "NONE", None, "Pocas velas (mín. 30)"
    if modo is None:
        modo = config.get("facundo_modo", "ambos")
    if invertir is None:
        invertir = bool(config.get("facundo_invertir", False))
    if usar_ema is None:
        usar_ema = bool(config.get("facundo_usar_ema", True))

    det = None
    if modo == "rechazo":
        det = detectar_facundo_rechazo(velas)
    elif modo == "rompimiento":
        det = detectar_facundo_rompimiento(velas)
    elif modo == "lateral":
        det = detectar_facundo_lateral(velas)
    else:  # "ambos"
        det_r = detectar_facundo_rechazo(velas)
        det_o = detectar_facundo_rompimiento(velas)
        if det_r and det_o:
            det = det_r if det_r["cuerpo_conf"] >= det_o["cuerpo_conf"] else det_o
        else:
            det = det_r or det_o
    if det is None:
        return "NONE", None, "Sin señal Facundo (sin nivel probado + mecha + confirmación)"

    direccion = det["dir"]
    inv_txt = ""
    if invertir:
        direccion = "PUT" if direccion == "CALL" else "CALL"
        inv_txt = " | INVERTIDO"

    # Filtro de tendencia sobre la dirección QUE SE VA A EJECUTAR.
    if usar_ema and not _facundo_filtro_ema(direccion, velas):
        return ("NONE", None,
                f"Señal Facundo-{det['tipo']} descartada: dirección final "
                f"{'CALL' if direccion == 'CALL' else 'PUT'} contra la tendencia EMA")

    dir_txt = "COMPRA" if direccion == "CALL" else "VENTA"
    razon = (f"{dir_txt} | FACUNDO-{det['tipo']} | nivel {det['nivel']} "
             f"(toque #{det.get('toques', '?')}) | cuerpo conf:{det['cuerpo_conf']}%"
             f"{inv_txt} | entrada vela siguiente")
    return (("BUY" if direccion == "CALL" else "SELL"), det, razon)


def _analizar_facundo(velas, par=None):
    """Estrategia FACUNDO (price action sobre líneas importantes).
    Wrapper del núcleo _facundo_decidir() con la config en vivo.
    Variantes en 'facundo_modo': rechazo / rompimiento / lateral / ambos.
    Con config['facundo_invertir']=True la dirección de entrada se
    invierte (elección del usuario tras sus pruebas en vivo); el filtro
    EMA valida la dirección final. La entrada real ocurre en la apertura
    de la vela siguiente (la maneja el loop).
    Devuelve (BUY|SELL|NONE, descripción)."""
    senal, _det, razon = _facundo_decidir(velas)
    return senal, razon


# =============================================================
# ESTRATEGIA: SINTETICO (índices sintéticos de Deriv)
#
# Los sintéticos NO son forex: son series generadas por un RNG con
# propiedades publicadas. Por eso esta estrategia NO usa S/R ni números
# redondos — usa la ESTRUCTURA matemática de cada índice:
#
#  · CRASH*: el precio SUBE de forma sostenida entre caídas (spikes)
#    que ocurren, en promedio, 1 vez cada N ticks (Crash 500 = 1/500).
#    En una vela M1 (~60 ticks) la probabilidad estructural de que NO
#    haya spike es alta -> se opera CALL, salvo justo tras un spike.
#  · BOOM*: espejo exacto -> se opera PUT.
#  · R_* / 1HZ* (Volatility): movimiento browniano puro. No hay
#    tendencia explotable; el motor de reversión entra contra extremos
#    estadísticos (z-score + racha) y sirve para MEDIR en el backtest
#    si tu cuenta/feed muestra algún borde real antes de arriesgar.
#
# ⚠ HONESTIDAD: en Boom/Crash el winrate estructural es alto pero el
# payout que Deriv ofrece por ir a favor de la deriva es BAJO (el
# precio del contrato ya descuenta la estructura). El backtest y el
# guard de payout (deriv_payout_min) están para que las decisiones se
# tomen con números medidos, no con ilusión. Valida SIEMPRE en demo.
# =============================================================

def _sintetico_tipo(par):
    """Clasifica el índice sintético: 'crash', 'boom', 'vol' u 'otro'."""
    p = (par or "").upper()
    if p.startswith("CRASH"):
        return "crash"
    if p.startswith("BOOM"):
        return "boom"
    if p.startswith("R_") or p.startswith("1HZ") or p.startswith("RDB"):
        return "vol"
    return "otro"


def _sintetico_hubo_spike(velas, tipo, factor, mirar):
    """True si en las últimas 'mirar' velas hubo un spike: una vela cuyo
    rango supera 'factor' × la mediana de rangos recientes, en la
    dirección del spike del índice (abajo en Crash, arriba en Boom)."""
    if len(velas) < 30:
        return True   # sin historia suficiente, mejor no operar
    rangos = sorted((v["max"] - v["min"]) for v in velas[-100:])
    mediana = rangos[len(rangos) // 2] or 1e-12
    for v in velas[-mirar:]:
        rango = v["max"] - v["min"]
        if rango > factor * mediana:
            bajista = v["close"] < v["open"]
            if (tipo == "crash" and bajista) or (tipo == "boom" and not bajista):
                return True
            # spike de rango sin dirección clara: también lo respetamos
            if abs(v["close"] - v["open"]) < 0.3 * rango:
                return True
    return False


def _sintetico_z_score(velas, periodo):
    """z-score del último cierre frente a la media/desviación de los
    'periodo' cierres anteriores. None si no hay datos suficientes."""
    if len(velas) < periodo + 1:
        return None
    cierres = [v["close"] for v in velas[-(periodo + 1):-1]]
    media = sum(cierres) / len(cierres)
    var = sum((c - media) ** 2 for c in cierres) / len(cierres)
    desv = var ** 0.5
    if desv <= 0:
        return None
    return (velas[-1]["close"] - media) / desv


def _sintetico_racha(velas):
    """(color, n): color de la última vela ('alcista'/'bajista') y cuántas
    velas seguidas del mismo color cierran la serie."""
    n = 0
    color = None
    for v in reversed(velas):
        c = "alcista" if v["close"] > v["open"] else ("bajista" if v["close"] < v["open"] else None)
        if c is None:
            break
        if color is None:
            color = c
        if c != color:
            break
        n += 1
    return color, n


def detectar_sintetico(velas, par=None,
                       spike_factor=None, post_spike=None,
                       z_periodo=None, z_umbral=None, racha_min=None):
    """Núcleo de la estrategia SINTETICO. Devuelve ("CALL"/"PUT", razon)
    o (None, razon). Parámetros explícitos para que el backtest pueda
    correr sin mutar la config global."""
    spike_factor = spike_factor if spike_factor is not None else config.get("sintetico_spike_factor", 5.0)
    post_spike   = post_spike   if post_spike   is not None else config.get("sintetico_post_spike_velas", 3)
    z_periodo    = z_periodo    if z_periodo    is not None else config.get("sintetico_z_periodo", 20)
    z_umbral     = z_umbral     if z_umbral     is not None else config.get("sintetico_z_umbral", 1.8)
    racha_min    = racha_min    if racha_min    is not None else config.get("sintetico_racha_min", 3)

    tipo = _sintetico_tipo(par)

    if tipo in ("crash", "boom"):
        # SPIKE-RIDE: a favor de la deriva estructural, nunca tras un spike.
        if _sintetico_hubo_spike(velas, tipo, spike_factor, max(1, int(post_spike))):
            return None, f"{par}: spike reciente — esperando {post_spike} velas"
        if tipo == "crash":
            return "CALL", (f"COMPRA | SPIKE-RIDE {par} | deriva alcista entre "
                            f"caídas, sin spike en las últimas {post_spike} velas")
        return "PUT", (f"VENTA | SPIKE-RIDE {par} | deriva bajista entre "
                       f"subidas, sin spike en las últimas {post_spike} velas")

    if tipo == "vol":
        # REVERSIÓN Z-SCORE: contra extremos estadísticos.
        z = _sintetico_z_score(velas, int(z_periodo))
        if z is None:
            return None, "historia insuficiente para z-score"
        color, n = _sintetico_racha(velas)
        if abs(z) < z_umbral or n < racha_min:
            return None, f"sin extremo (z={z:.2f}, racha {n})"
        if z > 0 and color == "alcista":
            return "PUT", (f"VENTA | REVERSIÓN {par} | z={z:.2f} sobre la media "
                           f"+ {n} velas alcistas seguidas — extremo estadístico")
        if z < 0 and color == "bajista":
            return "CALL", (f"COMPRA | REVERSIÓN {par} | z={z:.2f} bajo la media "
                            f"+ {n} velas bajistas seguidas — extremo estadístico")
        return None, f"z y racha no coinciden (z={z:.2f}, racha {color} {n})"

    return None, f"{par}: índice no reconocido como sintético"


def _analizar_sintetico(velas, par=None):
    """Wrapper con la firma que esperan loop_par/backtest: (señal, razón)."""
    direccion, razon = detectar_sintetico(velas, par=par)
    if direccion is None:
        return "NONE", razon
    return ("BUY" if direccion == "CALL" else "SELL"), razon


def analizar(velas, estado_senal=None, par=None):
    """Despachador de estrategia: enruta a la estrategia elegida en la
    config ("ifc", "ifcpro", "nr", "facundo" o "sintetico"). 'par' permite
    que IFC use el modo propio del par asignado por la auto-calibración.
    Mantiene la firma que esperan loop_par, el warmup y el backtest."""
    estrategia = config.get("estrategia", "ifc")
    if estrategia == "nr":
        return _analizar_nr(velas, par=par)
    if estrategia == "ifcpro":
        return _analizar_ifcpro(velas)
    if estrategia == "facundo":
        return _analizar_facundo(velas, par=par)
    if estrategia == "sintetico":
        return _analizar_sintetico(velas, par=par)
    return _analizar_ifc(velas, estado_senal, par)


# =============================================================
# ESTRATEGIA: NÚMERO REDONDO ("El Hermoso")
#
# Basada en la transcripción del curso "CURSO AVANZADO CLASE NUMERO 4
# NUMERO REDONDO EL HERMOSO" del trader TuEsposoTrader. La idea central:
# los niveles de precio "redondos" (cada 50/100 pips en forex) actúan
# como zonas institucionales donde se acumulan órdenes limitadas y el
# precio tiende a rebotar. Funciona como soporte/resistencia con reglas
# muy concretas que el trader explica en el video:
#
#   1) POLARIDAD: si el precio está por encima del nivel, solo se
#      COMPRA (rebote desde abajo). Si está por debajo, solo se VENDE.
#   2) RUPTURA: una vela con cuerpo decidido que cierra más allá del
#      nivel INVIETE la polaridad (el soporte se vuelve resistencia).
#   3) MÁXIMO 3 TOQUES: tras el 3er toque de un nivel, no se opera más
#      ese nivel hasta que haya ruptura. "Los números redondos no se
#      operan más de tres toques. Te va a sacar."
#   4) RELLENO DE MECHAS: el punto real de entrada se DESPLAZA según la
#      última mecha que cruzó el nivel. Si la mecha inferior atravesó
#      el nivel 5 pips y rebotó, el nivel real está 5 pips más abajo.
#   5) Confirmación: el trader espera ver la ruptura confirmada y entra
#      en la vela siguiente (igual que IFC).
# =============================================================

def _es_par_jpy(par):
    """Detecta si el par contiene JPY (necesita manejo especial de pips)."""
    return par is not None and "JPY" in par.upper()


def _tamanio_pip(par):
    """Tamaño de 1 pip en términos de precio del par.
    Pares JPY: 0.01 (un pip = 0.01 yen).
    Resto de forex: 0.0001 (un pip = 0.0001 unidades)."""
    return 0.01 if _es_par_jpy(par) else 0.0001


def _generar_niveles_redondos(precio_min, precio_max, par=None):
    """Genera todos los niveles redondos en el rango [precio_min, precio_max]
    según la configuración: 100 pips (más fuertes), 50 pips (intermedios)
    y opcionalmente 10 pips (ruido).

    Cada nivel es un float (precio exacto del nivel). El caller luego
    determina cuáles están 'cercanos' al precio actual."""
    pip = _tamanio_pip(par)
    niveles = set()
    # 100 pips: 0.0100 para forex, 1.00 para JPY
    if config.get("nr_niveles_100pips", True):
        paso = pip * 100
        inicio = math.ceil(precio_min / paso) * paso
        n = inicio
        while n <= precio_max + paso:
            niveles.add(round(n, 6))
            n += paso
    # 50 pips: 0.0050 / 0.50
    if config.get("nr_niveles_50pips", True):
        paso = pip * 50
        inicio = math.ceil(precio_min / paso) * paso
        n = inicio
        while n <= precio_max + paso:
            niveles.add(round(n, 6))
            n += paso
    # 10 pips: 0.0010 / 0.10 (mucho ruido en M1; off por defecto)
    if config.get("nr_niveles_10pips", False):
        paso = pip * 10
        inicio = math.ceil(precio_min / paso) * paso
        n = inicio
        while n <= precio_max + paso:
            niveles.add(round(n, 6))
            n += paso
    return sorted(niveles)


def _nivel_mas_cercano(precio, niveles):
    """Devuelve (nivel_mas_cercano, distancia_en_precio) o (None, None)
    si la lista está vacía."""
    if not niveles:
        return None, None
    nivel = min(niveles, key=lambda n: abs(n - precio))
    return nivel, abs(nivel - precio)


def _contar_toques(velas, nivel, par=None, ventana=None):
    """Cuenta cuántas velas (en la ventana) TOCARON el nivel como evento
    de rebote claro (no cualquier vela cercana).

    Según la regla del trader "los números redondos no se operan más de
    tres toques", un 'toque' es un evento donde la vela VIENE DE UN LADO,
    ALCANZA EL NIVEL y REBOTA — no cualquier vela que pase cerca. Para
    que cuente como toque, la vela debe:
      - Tener la mecha (superior o inferior) que ATRAVIESA el nivel o lo
        toca con precisión (mecha dentro de la tolerancia).
      - Y cerrar del LADO OPUESTO al que cruzó la mecha (rebote claro).
        Si la mecha inferior cruzó el nivel pero la vela cerró abajo
        también, no es un rebote — es una continuación bajista.

    Devuelve:
      {
        "toques": int,
        "ultimo_toque": "superior" | "inferior" | None,
        "penultimo_toque": "superior" | "inferior" | None,
        "velas_toques": [índices de las últimas velas que tocaron],
        "desplazamiento_mecha": float,  # pips, para el relleno
      }

    El "relleno de mechas" del trader: si la ÚLTIMA mecha que cruzó el
    nivel lo hizo por N pips, el nivel real está N pips desplazado en
    esa dirección. Lo devolvemos para que el caller ajuste el nivel.
    """
    if ventana is None:
        ventana = config.get("nr_ventana_toques", 15)
    pip = _tamanio_pip(par)
    tolerancia = config.get("nr_tolerancia_pips", 5) * pip
    dist_min = config.get("nr_distancia_min_toque_pips", 8) * pip
    velas_ventana = velas[-ventana:] if len(velas) > ventana else velas

    toques = []
    for i, v in enumerate(velas_ventana):
        # Toque INFERIOR (rebote desde abajo, soporte):
        # la mecha inferior (min) alcanzó o cruzó el nivel Y la vela cerró
        # por encima del nivel. Eso es un rechazo claro desde abajo.
        if v["min"] <= nivel + tolerancia and v["close"] > nivel:
            # La mecha inferior efectivamente cruzó o tocó el nivel
            if v["min"] <= nivel + tolerancia and v["min"] >= nivel - tolerancia * 3:
                # FILTRO: para contar como toque, el precio debe VENIR de
                # al menos nr_distancia_min_toque_pips más abajo en las
                # últimas 5 velas. Esto evita contar velas que solo
                # "pasan por" el nivel oscilando cerca de él.
                viene_de_lejos = False
                for k in range(max(0, i - 5), i):
                    if velas_ventana[k]["close"] <= nivel - dist_min:
                        viene_de_lejos = True
                        break
                if viene_de_lejos:
                    desplaz = (v["min"] - nivel) / pip
                    toques.append({"idx": i, "lado": "inferior", "desplaz_pips": desplaz, "vela": v})
        # Toque SUPERIOR (rebote desde arriba, resistencia):
        # la mecha superior (max) alcanzó o cruzó el nivel Y la vela cerró
        # por debajo del nivel.
        elif v["max"] >= nivel - tolerancia and v["close"] < nivel:
            if v["max"] >= nivel - tolerancia and v["max"] <= nivel + tolerancia * 3:
                # Filtro simétrico: el precio debe venir de al menos dist_min
                # más arriba en las últimas 5 velas.
                viene_de_lejos = False
                for k in range(max(0, i - 5), i):
                    if velas_ventana[k]["close"] >= nivel + dist_min:
                        viene_de_lejos = True
                        break
                if viene_de_lejos:
                    desplaz = (v["max"] - nivel) / pip
                    toques.append({"idx": i, "lado": "superior", "desplaz_pips": desplaz, "vela": v})

    if not toques:
        return {"toques": 0, "ultimo_toque": None, "penultimo_toque": None,
                "velas_toques": [], "desplazamiento_mecha": 0.0}

    # El desplazamiento del relleno de mechas: la ÚLTIMA mecha que cruzó
    # el nivel define cuánto se desplaza el nivel real. Si la última mecha
    # inferior cruzó 5 pips por debajo del nivel, el nivel real está 5 pips
    # más abajo (el precio rompió el nivel y se frenó un poco más allá).
    ultimo = toques[-1]
    desplaz = ultimo["desplaz_pips"] if config.get("nr_relleno_mechas", True) else 0.0
    penultimo = toques[-2]["lado"] if len(toques) >= 2 else None

    return {
        "toques": len(toques),
        "ultimo_toque": ultimo["lado"],
        "penultimo_toque": penultimo,
        "velas_toques": [t["idx"] for t in toques],
        "desplazamiento_mecha": round(desplaz, 2),
        "ultima_vela_toque": ultimo["vela"],
    }


def _es_ruptura(vela_anterior, vela_actual, nivel, par=None):
    """Detecta si la vela_actual es una vela de RUPTURA confirmada del
    nivel, según las reglas del trader:
      - cuerpo decidido (>= nr_ruptura_cuerpo_min del rango)
      - cierre más allá del nivel por al menos nr_ruptura_margen_pips

    Devuelve ("ALCISTA", "BAJISTA", None). 'ALCISTA' = ruptura hacia arriba
    (la polaridad del nivel pasa a soporte), 'BAJISTA' = ruptura hacia
    abajo (pasa a resistencia). None = no es ruptura.

    La vela_anterior debe estar del lado opuesto al de la ruptura (sin
    este cruce previo no hay ruptura, solo continuación).
    """
    pip = _tamanio_pip(par)
    cuerpo_min = config.get("nr_ruptura_cuerpo_min", 0.60)
    margen = config.get("nr_ruptura_margen_pips", 3) * pip
    rango = candle_range(vela_actual)
    if rango <= 0:
        return None
    cuerpo = body_size(vela_actual)
    if cuerpo / rango < cuerpo_min:
        return None
    # Ruptura alcista: la vela anterior cerró por debajo o en el nivel,
    # y la actual cierra por encima del nivel + margen.
    if vela_anterior["close"] <= nivel and vela_actual["close"] >= nivel + margen:
        return "ALCISTA"
    # Ruptura bajista: la vela anterior cerró por encima o en el nivel,
    # y la actual cierra por debajo del nivel - margen.
    if vela_anterior["close"] >= nivel and vela_actual["close"] <= nivel - margen:
        return "BAJISTA"
    return None


def detectar_numero_redondo(velas, par=None, modo=None):
    """Detector principal de la estrategia Número Redondo.
    'modo': "rebote"/"ruptura" explícito, o None → lee config['nr_modo']
    (el backtest lo pasa explícito para no mutar la config en vivo).
    Analiza las velas recientes y devuelve un dict con:
      {
        "nivel": float,                  # nivel redondo más cercano
        "distancia_pips": float,         # distancia del precio actual al nivel
        "polaridad": "ALCISTA"|"BAJISTA", # qué lado del nivel está el precio
        "toques": int,                   # nº de toques en la ventana
        "ruptura": "ALCISTA"|"BAJISTA"|None, # si la última vela rompió
        "nivel_ajustado": float,         # nivel después del relleno de mechas
        "dir": "CALL"|"PUT"|None,        # dirección de la entrada
        "razon": str,                    # explicación legible
      }
    Devuelve None si no hay suficiente contexto.
    """
    if len(velas) < 5:
        return None

    # Precio actual = cierre de la última vela
    precio_actual = velas[-1]["close"]

    # Generar niveles en un rango alrededor del precio actual (±200 pips)
    pip = _tamanio_pip(par)
    precio_min = precio_actual - 200 * pip
    precio_max = precio_actual + 200 * pip
    niveles = _generar_niveles_redondos(precio_min, precio_max, par)
    if not niveles:
        return None

    # Encontrar el nivel más cercano al precio actual
    nivel, distancia = _nivel_mas_cercano(precio_actual, niveles)
    if nivel is None:
        return None

    distancia_pips = distancia / pip
    tolerancia = config.get("nr_tolerancia_pips", 5)
    # Si el precio está demasiado lejos del nivel más cercano, no hay señal.
    if distancia_pips > tolerancia * 4:
        return None

    # Polaridad: por encima del nivel = "ALCISTA" (operamos compras en rebote),
    # por debajo = "BAJISTA" (operamos ventas en rebote).
    polaridad = "ALCISTA" if precio_actual > nivel else "BAJISTA"

    # Detectar ruptura en la última vela
    ruptura = _es_ruptura(velas[-2], velas[-1], nivel, par)

    # Contar toques en la ventana
    info_toques = _contar_toques(velas, nivel, par)

    # Nivel ajustado por relleno de mechas
    desplaz = info_toques.get("desplazamiento_mecha", 0.0)
    nivel_ajustado = nivel + desplaz * pip

    # Decidir dirección según el modo (parámetro explícito o config)
    if modo is None:
        modo = config.get("nr_modo", "rebote")
    n_toques = info_toques["toques"]
    max_toques = config.get("nr_max_toques", 3)

    dir_final = None
    razon = ""

    if modo == "ruptura":
        # Modo ruptura: solo entra si la última vela es ruptura confirmada.
        if ruptura is None:
            razon = "Sin ruptura confirmada"
        else:
            # Dirección de la continuación de la ruptura
            dir_final = "CALL" if ruptura == "ALCISTA" else "PUT"
            razon = f"Ruptura {ruptura} confirmada del nivel {nivel}"
    else:
        # Modo rebote (default del trader):
        # - Si hubo ruptura, NO operamos el rebote (cambió la polaridad).
        # - Si ya tiene max_toques toques, no se opera más.
        # - En caso contrario, entramos a favor de la polaridad vigente.
        if ruptura is not None:
            razon = f"Ruptura {ruptura} detectada — esperar nueva polaridad"
        elif n_toques >= max_toques:
            razon = f"Nivel {nivel} con {n_toques} toques — agotado (máx {max_toques})"
        else:
            # Polaridad vigente: ALISTA -> COMPRA (rebote desde abajo);
            # BAJISTA -> VENTA (rebote desde arriba).
            dir_final = "CALL" if polaridad == "ALCISTA" else "PUT"
            razon = (f"Rebote en nivel {nivel} ({polaridad}), "
                     f"{n_toques}/{max_toques} toques, ajuste mecha {desplaz:+.1f} pips")

    # Filtro EMA opcional (igual que IFC: solo entra a favor de tendencia)
    if dir_final is not None and config.get("nr_usar_ema", False):
        tend = tendencia_emas(velas)
        if tend is None:
            dir_final = None
            razon = "EMA sin datos suficientes"
        elif dir_final == "CALL" and tend != "ALCISTA":
            dir_final = None
            razon = f"Rebote alcista descartado por tendencia EMA {tend}"
        elif dir_final == "PUT" and tend != "BAJISTA":
            dir_final = None
            razon = f"Rebote bajista descartado por tendencia EMA {tend}"

    return {
        "nivel": nivel,
        "distancia_pips": round(distancia_pips, 2),
        "polaridad": polaridad,
        "toques": n_toques,
        "ruptura": ruptura,
        "nivel_ajustado": round(nivel_ajustado, 6),
        "dir": dir_final,
        "razon": razon,
        "info_toques": info_toques,
    }


def _analizar_nr(velas, estado_senal=None, par=None):
    """Wrapper de la estrategia Número Redondo para el despachador analizar().
    Devuelve (BUY|SELL|NONE, descripción). La entrada real ocurre en la
    apertura de la vela siguiente (igual que IFC)."""
    if len(velas) < 5:
        return "NONE", "Pocas velas para NR (mín. 5)"
    det = detectar_numero_redondo(velas, par=par)
    if det is None:
        return "NONE", "Sin nivel redondo cercano"
    if det["dir"] is None:
        return "NONE", det.get("razon", "Sin señal NR")
    dir_txt = "COMPRA" if det["dir"] == "CALL" else "VENTA"
    desc = (f"{dir_txt} | NR {det['nivel']} | {det['polaridad']} | "
            f"toques {det['toques']}/{config.get('nr_max_toques', 3)} | "
            f"dist {det['distancia_pips']} pips | {det['razon']}")
    return (("BUY" if det["dir"] == "CALL" else "SELL"), desc)


# =============================================================
# BACKTEST de la estrategia (sobre velas históricas)
# =============================================================
def _backtest_sobre_velas(velas, modo="continuidad", usar_ema=None, ventana=None):
    """Corre la lógica del bot sobre una lista de velas, con 'modo' y
    'usar_ema' EXPLÍCITOS (no muta la config global, así es seguro correrlo
    mientras el bot opera). A cada paso se le pasa solo una VENTANA de velas
    recientes (como el bot en vivo). Cuando hay patrón en la vela i (fuerza),
    la entrada es en la apertura de la vela i+1 y el resultado es cierre vs
    apertura de esa vela. Devuelve estadísticas. No usa red."""
    if ventana is None:
        ventana = config.get("velas_analisis", 250)
    if usar_ema is None:
        usar_ema = config.get("ifc_usar_ema", True)
    minimo = max(3, config.get("ifc_ema_lenta", 21) + 2)
    total = wins = losses = 0
    buys = win_buy = sells = win_sell = 0
    racha_actual = peor_racha = 0

    for i in range(minimo, len(velas) - 1):
        v = velas[max(0, i - ventana + 1) : i + 1]
        det = detectar_ifc(v)
        if det is None:
            continue
        dir_fuerza = det["dir"]
        if usar_ema:
            tend = tendencia_emas(v)
            if tend is None:
                continue
            if dir_fuerza == "CALL" and tend != "ALCISTA":
                continue
            if dir_fuerza == "PUT" and tend != "BAJISTA":
                continue
        # dirección final según el modo
        if modo == "reversion":
            direccion = "PUT" if dir_fuerza == "CALL" else "CALL"
        else:
            direccion = dir_fuerza

        entrada = velas[i + 1]
        if direccion == "CALL":
            gano = entrada["close"] > entrada["open"]; buys += 1;  win_buy  += 1 if gano else 0
        else:
            gano = entrada["close"] < entrada["open"]; sells += 1; win_sell += 1 if gano else 0
        total += 1
        if gano:
            wins += 1; racha_actual = 0
        else:
            losses += 1; racha_actual += 1
            peor_racha = max(peor_racha, racha_actual)

    winrate = round(wins / total * 100, 1) if total else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "winrate": winrate,
        "buys": buys, "wr_buy":  round(win_buy / buys * 100, 1) if buys else 0.0,
        "sells": sells, "wr_sell": round(win_sell / sells * 100, 1) if sells else 0.0,
        "peor_racha_perdidas": peor_racha,
    }


def _backtest_sintetico(velas, par, variante="normal",
                        spike_factor=None, post_spike=None,
                        z_periodo=None, z_umbral=None, racha_min=None):
    """Backtest de la estrategia SINTETICO sobre velas históricas.
    variante:
      "normal"    -> la estrategia tal cual (la que operaría el bot)
      "invertida" -> dirección contraria (para comparar en la tabla)
      "base"      -> SIN filtros: entra en TODAS las velas a favor de la
                     deriva (solo Boom/Crash). Es la línea base contra la
                     que se mide si los filtros aportan algo de verdad.
    Misma forma de salida que _backtest_sobre_velas (winrate, rachas...).
    """
    total = wins = losses = 0
    buys = win_buy = sells = win_sell = 0
    racha_actual = peor_racha = 0
    tipo = _sintetico_tipo(par)
    minimo = max(30, int(z_periodo or config.get("sintetico_z_periodo", 20)) + 2)

    for i in range(minimo, len(velas) - 1):
        v = velas[max(0, i - 200):i + 1]
        if variante == "base" and tipo in ("crash", "boom"):
            direccion = "CALL" if tipo == "crash" else "PUT"
        else:
            direccion, _r = detectar_sintetico(
                v, par=par, spike_factor=spike_factor, post_spike=post_spike,
                z_periodo=z_periodo, z_umbral=z_umbral, racha_min=racha_min)
            if direccion is None:
                continue
            if variante == "invertida":
                direccion = "PUT" if direccion == "CALL" else "CALL"

        entrada = velas[i + 1]
        if direccion == "CALL":
            gano = entrada["close"] > entrada["open"]; buys += 1;  win_buy  += 1 if gano else 0
        else:
            gano = entrada["close"] < entrada["open"]; sells += 1; win_sell += 1 if gano else 0
        total += 1
        if gano:
            wins += 1; racha_actual = 0
        else:
            losses += 1; racha_actual += 1
            peor_racha = max(peor_racha, racha_actual)

    winrate = round(wins / total * 100, 1) if total else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "winrate": winrate,
        "buys": buys, "wr_buy":  round(win_buy / buys * 100, 1) if buys else 0.0,
        "sells": sells, "wr_sell": round(win_sell / sells * 100, 1) if sells else 0.0,
        "peor_racha_perdidas": peor_racha,
    }


def _fetch_historico(par, cantidad):
    """Trae 'cantidad' velas M1 históricas de 'par' en lotes, hacia atrás
    en el tiempo (get_candles devuelve como máx ~1000 por llamada)."""
    if not _Iq:
        return []
    todas = {}
    endtime = int(time.time())
    intentos = 0
    while len(todas) < cantidad and intentos < 40:
        intentos += 1
        n = min(1000, cantidad - len(todas))
        with _buy_lock:
            completo, lote, exc = _con_timeout(_Iq.get_candles, 25, par, 60, n, endtime)
        if not completo or exc is not None or not lote:
            break
        for v in lote:
            todas[v["from"]] = v
        endtime = lote[0]["from"] - 1          # el próximo lote termina antes del primero de este
        if len(lote) < n:
            break
    return [todas[k] for k in sorted(todas)]


def backtest_ifc(par, cantidad_velas=3000):
    """Backtest de IFC sobre las últimas 'cantidad_velas' velas M1 de 'par'
    (datos REALES del bróker, vía la conexión del bot). Corre los dos modos
    —continuidad y reversión— para poder compararlos."""
    if not _Iq:
        return {"ok": False, "error": "El bot no está conectado. Dale a 'Iniciar bot' primero."}
    registrar_activos_operables([par])          # asegura que el par sea reconocido
    velas = _fetch_historico(par, cantidad_velas)
    if not velas or len(velas) < 100:
        return {"ok": False, "error": f"No pude traer suficientes velas de {par} (traje {len(velas)})."}
    usar_ema = config.get("ifc_usar_ema", True)
    return {
        "ok": True, "par": par, "velas": len(velas), "usar_ema": usar_ema,
        "break_even_87": 53.5,
        "continuidad": _backtest_sobre_velas(velas, modo="continuidad", usar_ema=usar_ema),
        "reversion":   _backtest_sobre_velas(velas, modo="reversion",   usar_ema=usar_ema),
    }


def _backtest_nr(velas, modo, par=None, ventana=None):
    """Backtest de la estrategia Número Redondo sobre una lista de velas
    M1, con el modo ("rebote"/"ruptura") EXPLÍCITO (no muta la config).
    Misma mecánica que las demás estrategias: señal en la vela i,
    entrada en la apertura de i+1, resultado = cierre vs apertura de
    esa vela (1 minuto después, expiración M1).

    Ya NO muta config['nr_modo']: el modo a probar se pasa explícito al
    detector (mismo patrón anti-race que _backtest_facundo), así es
    seguro correr este backtest mientras el bot opera NR en vivo.
    """
    if ventana is None:
        ventana = config.get("velas_analisis", 250)
    if len(velas) < 30:
        return {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "buys": 0, "wr_buy": 0.0, "sells": 0, "wr_sell": 0.0,
                "peor_racha_perdidas": 0}
    total = wins = losses = 0
    buys = win_buy = sells = win_sell = 0
    racha_actual = peor_racha = 0
    # Empezamos desde 'ventana' para que el detector tenga contexto,
    # y terminamos en len(velas)-1 porque la entrada es en i+1.
    for i in range(ventana, len(velas) - 1):
        v = velas[max(0, i - ventana + 1): i + 1]
        det = detectar_numero_redondo(v, par=par, modo=modo)
        if det is None or det["dir"] is None:
            continue
        direccion = det["dir"]   # "CALL" o "PUT"
        entrada = velas[i + 1]
        if direccion == "CALL":
            gano = entrada["close"] > entrada["open"]
            buys += 1
            win_buy += 1 if gano else 0
        else:
            gano = entrada["close"] < entrada["open"]
            sells += 1
            win_sell += 1 if gano else 0
        total += 1
        if gano:
            wins += 1; racha_actual = 0
        else:
            losses += 1; racha_actual += 1
            peor_racha = max(peor_racha, racha_actual)
    winrate = round(wins / total * 100, 1) if total else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "winrate": winrate,
        "buys": buys, "wr_buy":  round(win_buy / buys * 100, 1) if buys else 0.0,
        "sells": sells, "wr_sell": round(win_sell / sells * 100, 1) if sells else 0.0,
        "peor_racha_perdidas": peor_racha,
    }


def _backtest_ifcpro(velas, ventana=None):
    """Backtest de IFC-Pro: recorre las velas y cuando detectar_ifcpro
    dispara en la vela i (toque de la zona de activación), entra en la
    apertura de i+1 a favor de la fuerza. Un solo 'modo' (la estrategia es
    direccional por diseño). Devuelve stats con la misma forma que las otras."""
    if ventana is None:
        ventana = config.get("velas_analisis", 250)
    espera_max = int(config.get("ifcpro_espera_max", 8))
    minimo = espera_max + 6
    total = wins = losses = 0
    buys = win_buy = sells = win_sell = 0
    racha_actual = peor_racha = 0
    for i in range(minimo, len(velas) - 1):
        v = velas[max(0, i - ventana + 1): i + 1]
        det = detectar_ifcpro(v)
        if det is None:
            continue
        direccion = det["dir"]
        entrada = velas[i + 1]
        if direccion == "CALL":
            gano = entrada["close"] > entrada["open"]; buys += 1;  win_buy  += 1 if gano else 0
        else:
            gano = entrada["close"] < entrada["open"]; sells += 1; win_sell += 1 if gano else 0
        total += 1
        if gano:
            wins += 1; racha_actual = 0
        else:
            losses += 1; racha_actual += 1
            peor_racha = max(peor_racha, racha_actual)
    winrate = round(wins / total * 100, 1) if total else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "winrate": winrate,
        "buys": buys, "wr_buy":  round(win_buy / buys * 100, 1) if buys else 0.0,
        "sells": sells, "wr_sell": round(win_sell / sells * 100, 1) if sells else 0.0,
        "peor_racha_perdidas": peor_racha,
    }


def _backtest_facundo(velas, modo=None, ventana=None, invertir=None):
    """Backtest de la estrategia Facundo sobre una lista de velas M1.
    Recorre las velas; cuando _facundo_decidir dispara en la vela i (con
    velas[-1] = confirmación), entra en la apertura de i+1 a favor de la
    dirección FINAL (inversión y filtro EMA incluidos — la MISMA ruta de
    decisión que el vivo, así el backtest mide exactamente lo que el bot
    opera).
    'modo' e 'invertir': None → usa la config; el comparador de 8 filas
    los pasa explícitos. YA NO se muta la config global durante el
    backtest — es seguro correrlo mientras el bot opera en vivo."""
    if ventana is None:
        ventana = config.get("velas_analisis", 250)
    total = wins = losses = 0
    buys = win_buy = sells = win_sell = 0
    racha_actual = peor_racha = 0
    for i in range(ventana, len(velas) - 1):
        v = velas[max(0, i - ventana + 1): i + 1]
        senal, det, _rz = _facundo_decidir(v, modo=modo, invertir=invertir)
        if senal not in ("BUY", "SELL"):
            continue
        entrada = velas[i + 1]
        if senal == "BUY":
            gano = entrada["close"] > entrada["open"]; buys += 1;  win_buy  += 1 if gano else 0
        else:
            gano = entrada["close"] < entrada["open"]; sells += 1; win_sell += 1 if gano else 0
        total += 1
        if gano:
            wins += 1; racha_actual = 0
        else:
            losses += 1; racha_actual += 1
            peor_racha = max(peor_racha, racha_actual)
    winrate = round(wins / total * 100, 1) if total else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "winrate": winrate,
        "buys": buys, "wr_buy":  round(win_buy / buys * 100, 1) if buys else 0.0,
        "sells": sells, "wr_sell": round(win_sell / sells * 100, 1) if sells else 0.0,
        "peor_racha_perdidas": peor_racha,
    }


def backtest_estrategia(par, cantidad_velas=3000, estrategia=None):
    """Backtest generalizado sobre velas M1 REALES del bróker: corre los
    modos de la estrategia pedida ("ifc", "ifcpro", "nr" o "facundo")
    para compararlos."""
    estrategia = (estrategia or config.get("estrategia", "ifc")).lower()
    ok_con, err_con = _asegurar_conexion_datos()
    if not ok_con:
        return {"ok": False, "error": err_con}
    registrar_activos_operables([par])
    velas = _fetch_historico(par, cantidad_velas)
    if not velas or len(velas) < 100:
        return {"ok": False, "error": f"No pude traer suficientes velas de {par} (traje {len(velas)})."}
    if estrategia == "ifcpro":
        # IFC-Pro (pullback a la zona) comparada contra la IFC clásica
        # (entrada directa), para ver si esperar el retroceso mejora.
        usar_ema = config.get("ifc_usar_ema", True)
        modos = [
            {"nombre": "IFC-PRO (rompe nivel + retroceso a la zona)", **_backtest_ifcpro(velas)},
            {"nombre": "IFC clásica continuidad (entrada directa)",   **_backtest_sobre_velas(velas, "continuidad", usar_ema)},
        ]
    elif estrategia == "facundo":
        # Facundo: 8 filas — las 4 variantes (rechazo, rompimiento,
        # lateral, ambos) tanto en versión directa como invertida, así
        # el usuario ve cuál rinde mejor en su par y activa el flag
        # facundo_invertir en consecuencia.
        # La marca "(config actual)" va en la versión que el bot usará
        # en vivo: INVERTIDO si facundo_invertir=True, DIRECTO si False.
        invertir_default = bool(config.get("facundo_invertir", False))
        sufijo     = "" if invertir_default else " (config actual)"   # filas DIRECTO
        sufijo_inv = " (config actual)" if invertir_default else ""   # filas INVERTIDO
        modos = [
            {"nombre": "FACUNDO — Rechazo · DIRECTO"+sufijo,           **_backtest_facundo(velas, "rechazo",     invertir=False)},
            {"nombre": "FACUNDO — Rechazo · INVERTIDO"+sufijo_inv,     **_backtest_facundo(velas, "rechazo",     invertir=True)},
            {"nombre": "FACUNDO — Rompimiento · DIRECTO"+sufijo,       **_backtest_facundo(velas, "rompimiento", invertir=False)},
            {"nombre": "FACUNDO — Rompimiento · INVERTIDO"+sufijo_inv, **_backtest_facundo(velas, "rompimiento", invertir=True)},
            {"nombre": "FACUNDO — Lateral · DIRECTO"+sufijo,           **_backtest_facundo(velas, "lateral",     invertir=False)},
            {"nombre": "FACUNDO — Lateral · INVERTIDO"+sufijo_inv,     **_backtest_facundo(velas, "lateral",     invertir=True)},
            {"nombre": "FACUNDO — Ambos · DIRECTO"+sufijo,             **_backtest_facundo(velas, "ambos",       invertir=False)},
            {"nombre": "FACUNDO — Ambos · INVERTIDO"+sufijo_inv,       **_backtest_facundo(velas, "ambos",       invertir=True)},
        ]
    elif estrategia == "sintetico":
        tipo = _sintetico_tipo(par)
        if tipo in ("crash", "boom"):
            modos = [
                {"nombre": f"SPIKE-RIDE {tipo.upper()} (estrategia con filtros — la que opera el bot)",
                 **_backtest_sintetico(velas, par, "normal")},
                {"nombre": "LÍNEA BASE (todas las velas a favor de la deriva, sin filtros)",
                 **_backtest_sintetico(velas, par, "base")},
            ]
        else:
            modos = [
                {"nombre": "REVERSIÓN Z-SCORE (la que opera el bot)",
                 **_backtest_sintetico(velas, par, "normal")},
                {"nombre": "CONTINUIDAD Z-SCORE (invertida, para comparar)",
                 **_backtest_sintetico(velas, par, "invertida")},
            ]
    else:
        usar_ema = config.get("ifc_usar_ema", True)
        modos = [
            {"nombre": "CONTINUIDAD (a favor de la fuerza)",     **_backtest_sobre_velas(velas, "continuidad", usar_ema)},
            {"nombre": "REVERSIÓN (contra la fuerza)",           **_backtest_sobre_velas(velas, "reversion",   usar_ema)},
        ]
    return {"ok": True, "par": par, "velas": len(velas), "estrategia": estrategia,
            "break_even_87": 53.5, "modos": modos}


# =============================================================
# DETECCIÓN DE PARES DISPONIBLES + BACKTEST DE 1 DÍA POR PAR
# =============================================================
# Flujo pensado para el modo "manual":
#   1) El usuario pulsa "Detectar pares" en la UI.
#   2) El bot lista OTC + normales que la plataforma reporta como abiertos
#      ahora mismo, cada uno con su payout actual.
#   3) El usuario marca cuáles quiere operar y pulsa "Backtest 1 día".
#   4) Para cada par se corren los DOS modos (continuidad y reversión) sobre
#      las últimas ~1440 velas M1 (24h reales). Se sugiere el modo con
#      mejor winrate; el usuario decide si lo acepta o le pone el contrario.
#   5) La configuración por par queda guardada en config['ifc_modo_por_par']
#      y se aplica en caliente sin reiniciar el bot.

# ── Conexión de SOLO-DATOS (backtest/detección sin arrancar el bot) ──
# El usuario quiere poder detectar pares y correr backtests desde la
# interfaz ANTES de iniciar el bot. Estas operaciones solo necesitan
# leer velas/instrumentos/payouts, no operar. Si el bot no está
# corriendo y no hay conexión viva, se crea una conexión ligera con las
# credenciales guardadas y se asigna al mismo global _Iq (cuando el
# usuario inicie el bot después, loop_bot crea su propia conexión y la
# reemplaza — el flujo de siempre, sin conflicto).
_conexion_datos_lock = threading.Lock()

def _asegurar_conexion_datos():
    """Garantiza una conexión utilizable para DATOS (velas, instrumentos,
    payouts) sin necesidad de que el bot esté corriendo.
      - Ya hay conexión viva (del bot o de datos) → se usa esa.
      - Bot corriendo pero aún conectando → pedir reintento (su propia
        lógica de conexión/reconexión está en eso; no interferir).
      - Bot detenido y sin conexión → crear conexión de solo-datos.
    Devuelve (True, "") o (False, "mensaje para el usuario")."""
    global _Iq
    with _conexion_datos_lock:
        if _Iq is not None:
            try:
                completo, viva, _exc = _con_timeout(_Iq.check_connect, 5)
                if completo and viva:
                    return True, ""
            except Exception:
                pass
        with _lock:
            corriendo = bool(estado.get("corriendo"))
        if corriendo:
            return False, ("El bot está iniciando o reconectando. Espera unos "
                           "segundos a que diga 'Conectado' y reintenta.")
        cargar_config()
        faltan = _faltan_credenciales(config)
        if faltan:
            return False, faltan
        log("[DATOS] Conectando a la plataforma en modo solo-datos "
            "(backtest/detección sin iniciar el bot)...")
        try:
            iq = crear_conector(config, logger=log)
            completo, res, exc = _con_timeout(iq.connect, 45)
            if not completo:
                return False, "La conexión tardó demasiado. Revisa tu internet y reintenta."
            if exc is not None:
                return False, f"Error conectando: {exc}"
            ok, reason = (res if isinstance(res, tuple) else (bool(res), ""))
            if not ok:
                return False, f"La plataforma rechazó la conexión: {reason}"
            try:
                iq.change_balance(config.get("modo", "PRACTICE"))
            except Exception:
                pass   # para leer velas no es imprescindible
            _Iq = iq
            log("[DATOS] ✅ Conexión de solo-datos lista.")
            return True, ""
        except Exception as e:
            return False, f"No pude conectar: {e}"


def _payouts_actuales():
    """{nombre_activo: porcentaje_pago_0_a_100} usando get_all_profit()
    del fork Lu-Yi-Hsun. Devuelve {} si la librería no lo expone o falla.
    El valor devuelto por la librería suele venir como fracción (0.87);
    aquí lo pasamos a porcentaje entero (87)."""
    if not _Iq:
        return {}
    try:
        fn = getattr(_Iq, "get_all_profit", None)
        if fn is None:
            return {}
        completo, res, exc = _con_timeout(fn, 10)
        if not completo or exc is not None or not isinstance(res, dict):
            return {}
    except Exception:
        return {}
    payouts = {}
    for nombre, val in res.items():
        best = 0.0
        if isinstance(val, dict):
            for k in ("turbo", "binary"):
                try:
                    v = float(val.get(k, 0) or 0)
                    if v > best:
                        best = v
                except Exception:
                    pass
        else:
            try:
                best = float(val or 0)
            except Exception:
                best = 0.0
        # La librería a veces reporta 0-1 y a veces 0-100. Normalizamos.
        if best > 0 and best <= 1.5:
            best *= 100
        if best > 0:
            payouts[nombre] = int(round(best))
    return payouts


# ── Cache de payouts para el filtro en vivo (facundo_payout_min) ──────
# get_all_profit() puede tardar segundos; NO se debe llamar dentro de la
# ventana de análisis :57→:00 de loop_par. El refresco lo hace loop_bot
# (fuera de la ruta caliente) cada _PAYOUT_CACHE_TTL_S; loop_par solo LEE.
_PAYOUT_CACHE_TTL_S = 300
_payout_cache = {"ts": 0.0, "data": {}}
_payout_cache_lock = threading.Lock()
_payout_avisado = set()   # pares ya avisados en log (para no repetir cada vela)


def _refrescar_payout_cache(forzar=False):
    """Refresca el cache de payouts si está viejo. Llamar SOLO desde
    loop_bot u otro contexto fuera de la ventana de entrada. Nunca lanza."""
    ahora = time.time()
    with _payout_cache_lock:
        if not forzar and ahora - _payout_cache["ts"] < _PAYOUT_CACHE_TTL_S:
            return
    try:
        data = _payouts_actuales() or {}
    except Exception:
        data = {}
    with _payout_cache_lock:
        if data:
            _payout_cache["data"] = data
        _payout_cache["ts"] = ahora   # aunque falle, no reintentar cada 5s


def _payout_par_cacheado(par):
    """Payout (%) conocido del par según el último refresco, o None si
    no hay dato. Solo lee el cache — apto para la ruta caliente."""
    with _payout_cache_lock:
        return _payout_cache["data"].get(par)


def _facundo_payout_ok(par):
    """Aplica la regla del video: solo operar pares con payout >=
    config['facundo_payout_min']. Devuelve (ok, payout_o_None).
    FAIL-OPEN: si el payout del par no se conoce (la librería no lo
    reportó), NO bloquea la entrada — avisa una vez en el log y deja
    pasar; bloquear todo por un endpoint inestable sería peor."""
    pmin = float(config.get("facundo_payout_min", 0) or 0)
    if pmin <= 0:
        return True, None
    p = _payout_par_cacheado(par)
    if p is None:
        if par not in _payout_avisado:
            _payout_avisado.add(par)
            log(f"[FACUNDO] ⚠ Payout de {par} desconocido — el filtro "
                f"facundo_payout_min ({pmin:.0f}%) no se puede verificar; se opera igual.")
        return True, None
    return (p >= pmin), p


def detectar_pares_disponibles():
    """Lista todos los pares disponibles ahora mismo para el bot: OTC
    (siempre) + normales (solo si están abiertos). Cada entrada incluye
    tipo (otc/normal), nombre visible, payout actual, si está abierto y
    su modo IFC configurado (si ya lo tenía). No filtra por calidad ni
    payout: el usuario decide.
    Ya NO requiere que el bot esté corriendo: si está detenido, se abre
    una conexión de solo-datos con las credenciales guardadas."""
    ok_con, err_con = _asegurar_conexion_datos()
    if not ok_con:
        return {"ok": False, "error": err_con}

    # Registrar todos los activos que reporta el servidor (así buy() los
    # reconoce por nombre después, sin tener que reiniciar el bot).
    n_regs, por_nombre = registrar_activos_operables()
    if not por_nombre:
        return {"ok": False, "error":
                "No pude leer los instrumentos del servidor. Espera unos segundos con el bot conectado y reintenta."}

    payouts = _payouts_actuales()
    modos_par = config.get("ifc_modo_por_par") or {}

    otc, normales = [], []
    for nombre, info in por_nombre.items():
        abierto = bool(info.get("binary") or info.get("turbo"))
        entry = {
            "nombre_real": nombre,
            "display":     nombre[:-3] if nombre.endswith("-op") else nombre,
            "abierto":     abierto,
            "payout":      payouts.get(nombre, 0),
            "id":          info.get("id"),
            "modo":        modos_par.get(nombre) or _obtener_modo_par(nombre),
        }
        if str(nombre).endswith("-OTC"):
            entry["tipo"] = "otc"
            otc.append(entry)
        elif str(nombre).endswith("-op") or "-" not in nombre:
            # Mercado normal (con o sin sufijo -op). Solo lo listamos si
            # ahora está abierto — si el mercado normal está cerrado no
            # tiene sentido mostrar 20 pares en gris.
            if abierto:
                entry["tipo"] = "normal"
                normales.append(entry)
    otc.sort(key=lambda x: x["display"])
    normales.sort(key=lambda x: x["display"])
    return {"ok": True, "otc": otc, "normales": normales,
            "cuando": time.strftime("%H:%M:%S")}


def backtest_pares_1dia(pares, velas_n=1440):
    """Backtest masivo de 1 día (~1440 velas M1 reales) sobre la lista de
    pares, corriendo LA ESTRATEGIA ACTIVA de la config — el sentido del
    flujo es 'probar antes de arrancar el bot', así que se prueba lo que
    el bot va a operar:
      - ifc:     continuidad vs reversión (con sugerencia C/R aplicable
                 por par desde la UI, como siempre)
      - facundo: DIRECTO vs INVERTIDO (informativo — el flag
                 facundo_invertir es global, se cambia en config.json)
      - nr:      rebote vs ruptura (informativo — nr_modo es global)
      - ifcpro:  una sola variante (no tiene modos)
    Ya NO requiere el bot corriendo: si está detenido, se abre una
    conexión de solo-datos con las credenciales guardadas.
    Devuelve por par una lista de 'variantes' [{nombre, wr, total,
    peor_racha}], la sugerida, y el modo/config actual."""
    if not pares:
        return {"ok": False, "error": "Elige al menos un par para probar."}
    ok_con, err_con = _asegurar_conexion_datos()
    if not ok_con:
        return {"ok": False, "error": err_con}
    estr = (config.get("estrategia") or "ifc").lower()
    usar_ema = config.get("ifc_usar_ema", True)
    registrar_activos_operables(pares)
    resultados = []
    for par in pares:
        try:
            velas = _fetch_historico(par, velas_n)
        except Exception as e:
            resultados.append({"par": par, "ok": False, "error": str(e)})
            continue
        if not velas or len(velas) < 100:
            resultados.append({
                "par": par, "ok": False,
                "error": f"Histórico insuficiente ({len(velas) if velas else 0} velas)."
            })
            continue

        # ── Variantes según la estrategia activa ─────────────────────
        if estr == "facundo":
            d = _backtest_facundo(velas, modo=None, invertir=False)
            i = _backtest_facundo(velas, modo=None, invertir=True)
            variantes = [
                {"clave": "directo",   "nombre": "Directo",   "wr": d["winrate"], "total": d["total"], "peor_racha": d["peor_racha_perdidas"]},
                {"clave": "invertido", "nombre": "Invertido", "wr": i["winrate"], "total": i["total"], "peor_racha": i["peor_racha_perdidas"]},
            ]
            modo_actual = "invertido" if config.get("facundo_invertir") else "directo"
            aplicable = False   # el flag es global, no por par
        elif estr == "nr":
            rb = _backtest_nr(velas, "rebote",  par=par)
            rp = _backtest_nr(velas, "ruptura", par=par)
            variantes = [
                {"clave": "rebote",  "nombre": "Rebote",  "wr": rb["winrate"], "total": rb["total"], "peor_racha": rb["peor_racha_perdidas"]},
                {"clave": "ruptura", "nombre": "Ruptura", "wr": rp["winrate"], "total": rp["total"], "peor_racha": rp["peor_racha_perdidas"]},
            ]
            modo_actual = config.get("nr_modo", "rebote")
            aplicable = False   # nr_modo es global
        elif estr == "ifcpro":
            pro = _backtest_ifcpro(velas)
            variantes = [
                {"clave": "ifcpro", "nombre": "IFC-Pro", "wr": pro["winrate"], "total": pro["total"], "peor_racha": pro["peor_racha_perdidas"]},
            ]
            modo_actual = "ifcpro"
            aplicable = False
        else:  # ifc (default)
            c = _backtest_sobre_velas(velas, "continuidad", usar_ema)
            r = _backtest_sobre_velas(velas, "reversion",   usar_ema)
            variantes = [
                {"clave": "continuidad", "nombre": "Continuidad", "wr": c["winrate"], "total": c["total"], "peor_racha": c["peor_racha_perdidas"]},
                {"clave": "reversion",   "nombre": "Reversión",   "wr": r["winrate"], "total": r["total"], "peor_racha": r["peor_racha_perdidas"]},
            ]
            modo_actual = _obtener_modo_par(par)
            aplicable = True    # C/R sí se aplica por par

        # ── Sugerencia (misma regla para todas): muestra mínima y
        # diferencia clara; si no, sin recomendación. ─────────────────
        min_ops = 5
        total_ref = variantes[0]["total"]
        if len(variantes) < 2:
            sugerido, razon = None, ""
        elif total_ref < min_ops and variantes[1]["total"] < min_ops:
            sugerido, razon = None, f"solo {max(total_ref, variantes[1]['total'])} señales — muestra corta"
        else:
            mejor = max(variantes, key=lambda x: x["wr"])
            resto = [v for v in variantes if v is not mejor]
            if resto and abs(mejor["wr"] - resto[0]["wr"]) < 1.0:
                sugerido, razon = None, "empate técnico"
            elif mejor["total"] < min_ops:
                sugerido, razon = None, f"la mejor variante tiene solo {mejor['total']} señales"
            else:
                sugerido = mejor["clave"]
                razon = " vs ".join(f"{v['nombre']} {v['wr']}%" for v in variantes)

        fila = {
            "par": par, "ok": True, "velas": len(velas),
            "variantes": variantes,
            "sugerido": sugerido, "razon": razon,
            "modo_actual": modo_actual, "aplicable": aplicable,
        }
        # Compatibilidad con la UI/scripts que esperaban el formato IFC:
        if estr == "ifc":
            fila["total_senales"]  = variantes[0]["total"]
            fila["wr_continuidad"] = variantes[0]["wr"]
            fila["wr_reversion"]   = variantes[1]["wr"]
        resultados.append(fila)
    return {"ok": True, "estrategia": estr, "resultados": resultados,
            "velas_por_par": velas_n, "break_even_87": 53.5}


def set_modo_par(par, modo):
    """Guarda (persistente) el modo IFC de un par en particular. modo debe
    ser 'continuidad', 'reversion' o None (borrarlo). El cambio surte
    efecto EN CALIENTE en la próxima decisión de _analizar_ifc, sin
    reiniciar el bot ni el hilo del par."""
    if modo not in ("continuidad", "reversion", None, ""):
        return {"ok": False, "error": f"Modo inválido: {modo!r}"}
    mapa = dict(config.get("ifc_modo_por_par") or {})
    if not modo:
        mapa.pop(par, None)
    else:
        mapa[par] = modo
    config["ifc_modo_por_par"] = mapa
    guardar_config()
    return {"ok": True, "par": par, "modo": modo,
            "modo_efectivo": _obtener_modo_par(par)}


def verificar_stop(perdidas_seguidas):
    wins   = estado["wins"]
    losses = estado["losses"]
    if wins >= config["objetivo_dia"]:
        return "stop_objetivo"
    if losses >= config["max_perdidas_dia"]:
        return "stop_dia"
    if perdidas_seguidas >= config["max_perdidas_seguidas"]:
        return "stop_seguidas"
    return None

# =============================================================
# LOOP POR PAR (hilo independiente por cada par)
# =============================================================

def _calcular_monto_base(par=None):
    """Calcula el monto base para una nueva operación.

    Si config['position_sizing_activa'] es False, devuelve config['monto']
    (comportamiento original).

    Si está activo, usa Kelly fraccional:
        f_kelly = max(0, (p * payout - (1 - p)) / payout) * kelly_fraction
        monto   = clamp(balance * riesgo_pct% * f_kelly, min, max)
    donde p = winrate calibrado del par (o default_p si no hay).

    El 'balance' se lee del estado global bajo lock."""
    if not config.get("position_sizing_activa", False):
        return float(config.get("monto", 1.0))

    with _lock:
        balance = float(estado.get("balance", 0.0) or 0.0)
    if balance <= 0:
        # Sin balance conocido (modo PRACTICE recién arrancado, etc.):
        # caer al monto base para no quedarse sin operar.
        return float(config.get("monto", 1.0))

    # Winrate del par: si hay calibración guardada en _modo_por_par usamos
    # su valor; si no, el default. (Idealmente _modo_por_par debería
    # guardar también el wr; por ahora usamos default para no romper.)
    p = float(config.get("position_sizing_default_p", 0.52))
    payout = float(config.get("position_sizing_payout", 0.87))
    kelly_frac = float(config.get("position_sizing_kelly_frac", 0.25))
    riesgo_pct = float(config.get("position_sizing_riesgo_pct", 2.0))
    monto_min  = float(config.get("position_sizing_min", 1.0))
    monto_max  = float(config.get("position_sizing_max", 50.0))

    # Fórmula de Kelly para apuestas binarias:
    #   f = (p * b - q) / b   donde b = payout, q = 1 - p
    # Si p <= 1/(1+b), f <= 0 → no hay borde, apostamos el mínimo.
    b = payout
    q = 1.0 - p
    f_kelly_full = (p * b - q) / b if b > 0 else 0.0
    f_kelly = max(0.0, f_kelly_full) * kelly_frac

    if f_kelly <= 0:
        # Sin borde teórico: monto mínimo. No apostamos a perder.
        return max(monto_min, float(config.get("monto", 1.0)))

    monto = balance * (riesgo_pct / 100.0) * f_kelly
    # Clamp al rango permitido.
    monto = max(monto_min, min(monto_max, monto))
    # Redondeo a 2 decimales (IQ Option no acepta centavos fraccionales).
    return round(monto, 2)


def _limitar_martingala(monto_actual_acumulado, siguiente_monto):
    """Limita la exposición total de una racha de martingala.

    'monto_actual_acumulado' = suma de montos ya comprometidos en esta
    racha (monto base + reentradas anteriores). 'siguiente_monto' = el
    monto que el bot querría apostar en la próxima reentrada.

    Si monto_actual_acumulado + siguiente_monto supera
    config['martingala_max_exposicion_usd'], devuelve None indicando
    'resetear a monto base' (es decir, no seguir la martingala).
    En caso contrario devuelve siguiente_monto tal cual."""
    max_exp = float(config.get("martingala_max_exposicion_usd", 0) or 0)
    if max_exp <= 0:
        return siguiente_monto   # límite desactivado
    if monto_actual_acumulado + siguiente_monto > max_exp:
        return None
    return siguiente_monto


def _ejecutar_orden(par, dir_iq, senal, monto_orden, expiracion, ahora, idx):
    """
    Ejecuta una orden en IQ Option y espera su resultado.
    Devuelve (ganancia, balance) o (None, None) si hubo error.

    TIMING-FIX: antes había un time.sleep(idx * 0.3) aquí para "serializar"
    las órdenes entre pares. Pero las órdenes ya están serializadas por
    _buy_lock (cada buy() toma el lock), así que ese sleep era innecesario
    y causaba hasta 1.2s de retraso (con 4 pares). Eliminado.
    El _buy_lock se encarga de la serialización: si dos pares señalan en
    la misma vela, uno compra primero y el otro espera al lock (milisegundos,
    no segundos).
    """

    # ── LÍMITE DE EXPOSICIÓN TOTAL ────────────────────────────────
    # Antes de comprar: comprobar que la suma de montos en vuelo no
    # supere el límite configurado. Si lo supera, rechazar la entrada.
    if config.get("exposicion_max_pct", 0) > 0:
        with _lock:
            balance_actual = estado.get("balance", 0.0)
            exp_actual = estado.get("exposicion_en_vuelo", 0.0)
        if balance_actual > 0:
            limite = balance_actual * (config["exposicion_max_pct"] / 100.0)
            if exp_actual + monto_orden > limite + 0.01:  # +0.01 tolerancia redondeo
                log(f"[EXPOSICION] {par} — entrada ${monto_orden} rechazada: "
                    f"en vuelo ${exp_actual:.2f} + ${monto_orden:.2f} > límite ${limite:.2f} "
                    f"({config['exposicion_max_pct']}% de ${balance_actual:.2f}).")
                return None, None
    # Sumar al contador de exposición en vuelo.
    with _lock:
        estado["exposicion_en_vuelo"] = round(
            estado.get("exposicion_en_vuelo", 0.0) + monto_orden, 2)
        estado["martingala_monto"] = monto_orden

    ahora_entrada = datetime.datetime.utcnow()
    log(f"[ENTRY] {par} | {ahora_entrada.strftime('%H:%M:%S')} | seg {ahora_entrada.second} | monto:${monto_orden}")
    try:
        with _buy_lock:
            ok, order_id = _Iq.buy(monto_orden, par, dir_iq, expiracion)
    except Exception as e:
        log(f"[ERROR] {par} — al ejecutar orden: {e}")
        # Revertir exposición: la orden no se ejecutó.
        with _lock:
            estado["exposicion_en_vuelo"] = max(
                0.0, estado.get("exposicion_en_vuelo", 0.0) - monto_orden)
        return None, None

    if not ok:
        log(f"[ERROR] {par} — orden rechazada: {order_id}")
        with _lock:
            estado["exposicion_en_vuelo"] = max(
                0.0, estado.get("exposicion_en_vuelo", 0.0) - monto_orden)
        return None, None

    log(f"[OK] {par} — operación ejecutada. ID: {order_id}")
    threading.Thread(target=enviar_telegram, daemon=True, args=(
        f"✅ <b>ENTRADA EJECUTADA</b>\n"
        f"Par: <b>{par}</b>\n"
        f"Dirección: <b>{'COMPRA 🟢' if senal == 'BUY' else 'VENTA 🔴'}</b>\n"
        f"Monto: <b>${monto_orden}</b>\n"
        f"Expiración: <b>{expiracion} min</b>\n"
        f"Hora: {ahora_entrada.strftime('%H:%M:%S')}",
    )).start()

    # Nota: esta espera NO se interrumpe si se pide "detener" el bot —
    # hay una operación real con dinero ya comprometido, así que se deja
    # terminar siempre. Por eso "Detener" puede tardar hasta ~expiracion
    # minutos en completarse del todo si justo hay una orden abierta.
    time.sleep(expiracion * 60 + 5)
    # RELIABILITY: check_win_v3 y get_balance SIN timeout podian colgar
    # el hilo del par para siempre si IQ Option no respondia. Ahora los
    # envolvemos con _con_timeout. Si la primera llamada cae, reintentamos
    # hasta 3 veces con espera creciente antes de declarar la operación
    # como "resultado desconocido" — el hilo sigue vivo y el par sigue
    # operando, en vez de quedarse zombi.
    ganancia = None
    balance  = None
    for intento in range(3):
        completo_g, g_val, g_exc = _con_timeout(_Iq.check_win_v3, 15, order_id)
        if completo_g and g_exc is None and g_val is not None:
            ganancia = g_val
            break
        log(f"[WARN] {par} — check_win_v3 colgado/fallido (intento {intento+1}/3): {g_exc}")
        time.sleep(3 + intento * 2)
    # Sea cual sea el resultado, la operación YA cerró: liberar exposición.
    with _lock:
        estado["exposicion_en_vuelo"] = max(
            0.0, estado.get("exposicion_en_vuelo", 0.0) - monto_orden)
    if ganancia is None:
        log(f"[ERROR] {par} — no pude verificar el resultado de la orden {order_id} tras 3 intentos. El hilo sigue vivo; el resultado se cuenta como empate neutro.")
        # Devolvemos 0.0 como empate neutro para no romper la contabilidad.
        # El usuario puede revisar la operacion manualmente en la plataforma.
        return 0.0, None
    # get_balance con timeout y reintento
    for intento in range(3):
        completo_b, b_val, b_exc = _con_timeout(_Iq.get_balance, 10)
        if completo_b and b_exc is None and b_val is not None:
            balance = b_val
            break
        log(f"[WARN] {par} — get_balance colgado/fallido (intento {intento+1}/3): {b_exc}")
        time.sleep(2 + intento * 2)
    if balance is None:
        log(f"[WARN] {par} — no pude leer el balance tras 3 intentos. La operación se contabiliza pero el balance mostrado quedará desactualizado hasta la próxima lectura.")
    with _lock:
        if balance is not None:
            estado["balance"] = round(balance, 2)
        estado["neto"]    = round(estado["neto"] + ganancia, 2)
    return ganancia, balance


def loop_par(par, idx=0, mi_run_id=None):
    ultima_vela = None
    perdidas_seguidas_par = 0
    monto_actual_par = _calcular_monto_base(par)
    nivel_mart_par   = 0
    estado_senal_par = {"tipo": None, "velas_espera": 0, "velas_desde_ultima": 99}

    # Martingala pendiente: si es True, en la próxima vela se entra sin esperar señal
    mart_pendiente     = False
    mart_dir_iq        = None   # "call" o "put" — misma dirección que la pérdida anterior
    mart_senal         = None   # "BUY" o "SELL" — para los logs y Telegram

    # WATCHDOG: primer heartbeat para que el watchdog no marque este par
    # como "atascado" mientras todavía está en warmup.
    _heartbeat(par)

    log(f"[{par}] Hilo iniciado.")

    # ══════════════════════════════════════════════════════════════
    # WARM-UP: simulación histórica de 30 velas antes de operar
    #
    # El bot NO entra en ninguna operación durante este período.
    # Simula vela a vela cómo se habría comportado la estrategia
    # en el mercado reciente, calcula un mini-resultado histórico
    # y espera 2 minutos para que el contexto se estabilice.
    # ══════════════════════════════════════════════════════════════
    VELAS_WARMUP   = config.get("warmup_velas", 30)
    PAUSA_WARMUP_S = config.get("warmup_pausa_segundos", 120)   # 2 minutos por defecto

    warmup_ok = False
    while estado["corriendo"] and _run_id == mi_run_id and not warmup_ok and par not in _pares_desactivados:
        # WATCHDOG: actualizar heartbeat al INICIO de cada iteración del warmup
        # para que el watchdog no dispare durante el warmup (que puede tardar
        # 2+ minutos con la pausa de estabilización).
        _heartbeat(par)
        try:
            if not _Iq or not _Iq.check_connect():
                time.sleep(3)
                continue

            with _lock:
                estado["pares_estado"][par] = {"senal": "⏳ WARMUP"}

            log(f"[WARMUP] {par} — descargando historial ({config['velas_analisis']} velas)...")
            enviar_telegram(
                f"🔍 <b>Analizando mercado — {par}</b>\n"
                f"Simulando las últimas <b>{VELAS_WARMUP} velas</b>...\n"
                f"El bot comenzará a operar en ~{PAUSA_WARMUP_S // 60} min."
            )

            try:
                with _buy_lock:
                    velas_hist = _Iq.get_candles(par, 60, config["velas_analisis"], time.time())
            except Exception as e:
                log(f"[WARMUP] {par} — error obteniendo velas: {e}")
                time.sleep(5)
                continue

            if not velas_hist or len(velas_hist) < max(MACD_SLOW + MACD_SIGNAL, EMA_TREND) + VELAS_WARMUP + 10:
                log(f"[WARMUP] {par} — historial insuficiente, reintentando...")
                time.sleep(5)
                continue

            # ── Contexto actual (tendencia y volatilidad) ──────────
            cierres_all   = [v["close"] for v in velas_hist]
            ema50_actual  = calcular_ema(cierres_all, EMA_TREND)
            precio_actual = cierres_all[-1]
            tendencia_actual = "ALCISTA" if ema50_actual and precio_actual > ema50_actual else "BAJISTA"
            rango_prom = sum(v["max"] - v["min"] for v in velas_hist[-10:]) / 10

            # ── Simulación vela a vela sobre las últimas VELAS_WARMUP ──
            # Usamos todas las velas como contexto de indicadores,
            # pero solo "jugamos" las últimas VELAS_WARMUP.
            base_idx = len(velas_hist) - VELAS_WARMUP   # índice de inicio de simulación

            sim_estado  = {"tipo": None, "velas_espera": 0, "velas_desde_ultima": 99}
            sim_wins    = 0
            sim_losses  = 0
            sim_senales = []   # lista de dicts con cada señal detectada

            for i in range(VELAS_WARMUP):
                # Ventana de velas hasta la vela i-ésima del warmup
                ventana = velas_hist[: base_idx + i + 1]
                senal_sim, razon_sim = analizar(ventana, sim_estado, par)

                if senal_sim in ("BUY", "SELL"):
                    # La "entrada" sería en la vela siguiente (i+1)
                    if base_idx + i + 1 < len(velas_hist):
                        vela_resultado = velas_hist[base_idx + i + 1]
                        # Simular resultado: si la dirección coincide con el movimiento real
                        if senal_sim == "BUY":
                            gano = vela_resultado["close"] > vela_resultado["open"]
                        else:
                            gano = vela_resultado["close"] < vela_resultado["open"]

                        if gano:
                            sim_wins += 1
                            resultado_txt = "✅ WIN"
                        else:
                            sim_losses += 1
                            resultado_txt = "❌ LOSS"

                        sim_senales.append({
                            "vela": i + 1,
                            "direccion": senal_sim,
                            "resultado": resultado_txt,
                        })
                        log(f"[WARMUP-SIM] {par} vela {i+1}/{VELAS_WARMUP} → {senal_sim} → {resultado_txt} | {razon_sim}")

            # ── Análisis de volatilidad ────────────────────────────
            mercado_activo = rango_prom > 0.00005

            # ── Calcular tasa de acierto de la simulación ──────────
            sim_total = sim_wins + sim_losses
            tasa = f"{(sim_wins / sim_total * 100):.0f}%" if sim_total > 0 else "N/A"
            tasa_num = round(sim_wins / sim_total * 100, 1) if sim_total > 0 else None

            # ── Guardar resultado para el ranking consolidado entre pares ──
            with _lock:
                estado["warmup_resultados"][par] = {
                    "wins": sim_wins, "losses": sim_losses, "total": sim_total,
                    "tasa": tasa_num, "tendencia": tendencia_actual,
                    "mercado_activo": mercado_activo,
                }
                pares_esperados = estado.get("pares_operando") or config["pares"]
                todos_listos = all(p in estado["warmup_resultados"] for p in pares_esperados)
                ya_enviado   = estado["warmup_ranking_enviado"]

            if todos_listos and not ya_enviado:
                with _lock:
                    estado["warmup_ranking_enviado"] = True
                _enviar_ranking_warmup()

            # ── Inicializar estado de señal con contexto real ───────
            # (le decimos que ya pasaron VELAS_WARMUP velas desde la última señal)
            estado_senal_par["velas_desde_ultima"] = VELAS_WARMUP
            estado_senal_par["tipo"]         = None
            estado_senal_par["velas_espera"] = 0

            # ── Log y Telegram con resumen completo ────────────────
            detalle_senales = ""
            for s in sim_senales[-5:]:   # últimas 5 señales para no saturar el mensaje
                detalle_senales += f"\n  · Vela {s['vela']}: {s['direccion']} → {s['resultado']}"

            resumen_warmup = (
                f"📊 <b>Análisis previo completado — {par}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Velas analizadas: <b>{VELAS_WARMUP}</b>\n"
                f"Tendencia actual: <b>{tendencia_actual}</b> | EMA50: {ema50_actual:.5f}\n"
                f"Volatilidad (rango): <b>{rango_prom:.5f}</b> {'✅ Activo' if mercado_activo else '⚠️ Bajo'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 <b>Simulación histórica</b>\n"
                f"Señales detectadas: <b>{sim_total}</b>\n"
                f"Ganadas: <b>{sim_wins}</b> | Perdidas: <b>{sim_losses}</b>\n"
                f"Tasa de acierto: <b>{tasa}</b>"
                + (detalle_senales if detalle_senales else "\n  (sin señales en el período)")
                + f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ Esperando {PAUSA_WARMUP_S // 60} min antes de operar..."
            )
            log(f"[WARMUP] {par} OK | tendencia:{tendencia_actual} | sim {sim_wins}W/{sim_losses}L ({tasa}) | rango:{rango_prom:.5f}")
            enviar_telegram(resumen_warmup)

            # ── Pausa de estabilización (1-2 minutos configurables) ─
            # En trozos de 10s para poder reaccionar si se detiene el bot
            # o si arranca una nueva generación (Detener + Iniciar).
            pausa_total = PAUSA_WARMUP_S + (60 if not mercado_activo else 0)
            if not mercado_activo:
                log(f"[WARMUP] {par} — baja volatilidad, extendiendo pausa...")
                enviar_telegram(f"⚠️ <b>{par}</b> — Baja volatilidad. Pausa extendida 1 min extra.")
            restante = pausa_total
            while restante > 0 and estado["corriendo"] and _run_id == mi_run_id:
                time.sleep(min(10, restante))
                restante -= 10
                with _lock:
                    estado["pares_estado"][par] = {"senal": f"⏳ {restante}s"}

            with _lock:
                estado["pares_estado"][par] = {"senal": "✅ LISTO"}
            log(f"[WARMUP] {par} — pausa completada. Iniciando operativa en vivo.")
            warmup_ok = True

        except Exception:
            log(f"[WARMUP] {par} — error: " + traceback.format_exc())
            time.sleep(5)

    # El hilo también se detiene solo si la rotación automática apaga este
    # par (par in _pares_desactivados). Como la condición se evalúa ENTRE
    # iteraciones y una operación completa (compra → espera → resultado)
    # ocurre dentro de UNA iteración, nunca se abandona una compra a mitad.
    while estado["corriendo"] and _run_id == mi_run_id and par not in _pares_desactivados:
        # WATCHDOG: heartbeat al INICIO de cada iteración. Antes estaba al
        # final, pero los `continue` (SKIP, pierde prioridad, par bloqueado,
        # etc.) lo saltaban y el watchdog disparaba falsos positivos. Ahora
        # se actualiza SIEMPRE al empezar la vuelta, sin importar qué pasó
        # en la iteración anterior.
        _heartbeat(par)
        try:
            # --- Verificar si el par está bloqueado ---
            if par_esta_bloqueado(par):
                mart_pendiente = False   # cancelar martingala pendiente si el par se bloqueó
                with _lock:
                    estado["pares_estado"][par] = {"senal": "🚫 BLOQUEADO"}
                time.sleep(30)
                continue

            if not _Iq or not _Iq.check_connect():
                time.sleep(3)
                continue

            # ── SINCRONIZACIÓN CON LA VELA ──────────────────────────────
            # Antes: se pedían velas a cualquier segundo → la señal saltaba
            # con la vela a medio formar y la compra competía por el lock
            # justo al segundo 00 (entradas al :03, :11...). Ahora cada par
            # espera al segundo de análisis (~:55) de la vela actual: la
            # FUERZA llega ~92% formada, el análisis termina antes de :59 y
            # al segundo 00 el lock está libre → la compra dispara EXACTA.
            seg_obj = min(float(config.get("analisis_segundo", 55)) + idx * 0.8, 58.5)
            esperar_hasta_segundo(seg_obj)
            minuto_analisis = int(time.time() // 60)   # vela que se analiza
            # get_candles() de la librería tiene un bucle interno que puede
            # quedarse COLGADO PARA SIEMPRE si el internet se cae a mitad de
            # la petición (nunca llega respuesta y check_connect() puede
            # seguir diciendo "conectado"). Con timeout: si no responde,
            # forzamos reconexión limpia y reintentamos.
            #
            # TIMING-FIX: antes este get_candles tomaba _buy_lock, lo que
            # SERIALIZABA las peticiones de velas de los 4 pares. Si el par 0
            # tardaba 300ms, el par 3 tenía que esperar 1.2s y recién podía
            # analizar a las :58.4 — al borde del segundo 00. Ahora sin lock:
            # los 4 pares piden velas en paralelo y analizan a la vez.
            # El _buy_lock solo se necesita para buy() (compra real), no para
            # lectura de velas.
            try:
                completo_v, velas, exc_v = _con_timeout(
                    _Iq.get_candles, 20, par, 60, config["velas_analisis"], time.time())
            except Exception as e:
                log(f"[{par}] Error obteniendo velas: {e}")
                time.sleep(5)
                continue
            if not completo_v:
                log(f"[{par}] get_candles() sin respuesta (>20s): conexión colgada. Forzando reconexión.")
                _forzar_reconexion(f"{par}: get_candles colgado")
                time.sleep(3)
                continue
            if exc_v is not None:
                log(f"[{par}] Error obteniendo velas: {exc_v}")
                time.sleep(5)
                continue

            if not velas or len(velas) < 50:
                time.sleep(2)
                continue

            vela_id = velas[-1]["from"]
            if ultima_vela == vela_id:
                time.sleep(1)
                continue
            ultima_vela = vela_id

            # ══════════════════════════════════════════════════════════════
            # VELA INSTITUCIONAL: si la vela que recién cerró tiene un
            # rango muchísimo más grande que lo normal (noticia fuerte,
            # apertura/cierre de sesión, movimiento abrupto), se bloquea
            # el par para dejar que se estabilice antes de seguir operando.
            # ══════════════════════════════════════════════════════════════
            if config.get("vela_institucional_activa", True):
                es_inst, rango_vela, rango_prom = detectar_vela_institucional(
                    velas,
                    periodo=config.get("vela_institucional_periodo", 20),
                    multiplicador=config.get("vela_institucional_multiplicador", 3.5),
                )
                if es_inst:
                    log(f"[{par}] ⚡ Vela institucional detectada — rango {rango_vela:.5f} "
                        f"vs promedio {rango_prom:.5f} (x{rango_vela / rango_prom:.1f}). Bloqueando par.")
                    enviar_telegram(
                        f"⚡ <b>Vela institucional detectada</b>\n"
                        f"Par: <b>{par}</b>\n"
                        f"Rango de la vela: {rango_vela:.5f} (vs. promedio {rango_prom:.5f})\n"
                        f"Se bloquea el par para dejar que el mercado se estabilice."
                    )
                    mart_pendiente = False   # cancelar martingala pendiente: el contexto cambió de golpe
                    bloquear_par(par)
                    with _lock:
                        estado["pares_estado"][par] = {"senal": "⚡ VELA INSTITUCIONAL — bloqueado"}
                    continue

            ahora = datetime.datetime.now()
            expiracion = config["timeframe"] // 60

            # ══════════════════════════════════════════════════════════════
            # MARTINGALA INMEDIATA: si hay una entrada pendiente por pérdida,
            # entrar en esta vela en la misma dirección SIN esperar señal.
            # ══════════════════════════════════════════════════════════════
            if mart_pendiente and config.get("martingala_activa", False):
                mart_pendiente = False
                monto_orden    = round(monto_actual_par, 2)

                dir_txt = "🟢 COMPRA (CALL)" if mart_senal == "BUY" else "🔴 VENTA (PUT)"
                log(f"[MART] {par} — reentrada automática {dir_txt} × ${monto_orden}")
                enviar_telegram(
                    f"🔁 <b>MARTINGALA — Reentrada automática</b>\n"
                    f"Par: <b>{par}</b>\n"
                    f"Dirección: <b>{dir_txt}</b> (misma que pérdida anterior)\n"
                    f"Monto: <b>${monto_orden}</b> (nivel {nivel_mart_par})\n"
                    f"Esperando apertura de vela..."
                )

                with _lock:
                    estado["martingala_nivel"] = nivel_mart_par

                # Esperar segundo 00
                wait_secs = segundos_para_nueva_vela()
                log(f"[TIMING] {par} — MART esperando {wait_secs:.1f}s al segundo 00...")
                esperar_apertura_vela()

                ahora_check = datetime.datetime.utcnow()
                if ahora_check.second != 0:
                    log(f"[SKIP] {par} — MART no alcanzó el segundo 00. Reintento en próxima vela.")
                    mart_pendiente = True   # volver a intentar en la siguiente vela
                    continue

                ganancia, balance = _ejecutar_orden(par, mart_dir_iq, mart_senal, monto_orden, expiracion, ahora, idx)
                if ganancia is None:
                    continue

                # Procesar resultado de la martingala
                if ganancia > 0:
                    # GANADA → reset todo
                    perdidas_seguidas_par = 0
                    monto_actual_par = _calcular_monto_base(par)
                    nivel_mart_par   = 0
                    with _lock:
                        estado["wins"]             += 1
                        estado["perdidas_seguidas"] = 0
                        estado["martingala_nivel"]  = 0
                        estado["martingala_monto"]  = config["monto"]
                    log(f"[WIN-MART] ✅ {par} +${ganancia:.2f} | Wins hoy: {estado['wins']}/{config['objetivo_dia']} | Bal: ${balance:.2f}")
                    enviar_telegram(
                        f"🏆 <b>MARTINGALA GANADA ✅</b>\n"
                        f"Par: <b>{par}</b>\n"
                        f"Dirección: {dir_txt}\n"
                        f"Ganancia: <b>+${ganancia:.2f}</b>\n"
                        f"Balance: <b>${balance:.2f}</b>\n"
                        f"Wins hoy: {estado['wins']}/{config['objetivo_dia']}"
                    )
                    op = {"par": par, "direccion": mart_senal, "monto": monto_orden,
                          "resultado": "win", "ganancia": round(ganancia, 2),
                          "balance": round(balance, 2), "fecha": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                          "estrategia": config.get("estrategia", ""),
                          "racha_perdidas_momento": perdidas_seguidas_par}
                    with _lock:
                        estado["operaciones"].insert(0, op)

                elif ganancia < 0:
                    # PERDIDA en martingala
                    perdidas_seguidas_par += 1

                    if nivel_mart_par < config.get("martingala_niveles", 1):
                        # Quedan niveles → preparar otra reentrada inmediata
                        nivel_mart_par  += 1
                        nuevo_monto = round(monto_actual_par * config.get("martingala_mult", 2.0), 2)
                        # SAFETY: limitar la exposición total de la racha de
                        # martingala. Si seguir duplicando excedería el límite
                        # configurado, reseteamos a monto base y no seguimos
                        # la martingala esta vez (más seguro que arruinarse).
                        monto_acumulado = sum([_calcular_monto_base(par)] + [
                            _calcular_monto_base(par) * (config.get("martingala_mult", 2.0) ** i)
                            for i in range(nivel_mart_par - 1)
                        ])
                        limitado = _limitar_martingala(monto_acumulado, nuevo_monto)
                        if limitado is None:
                            log(f"[MART-LIMITE] {par} nivel {nivel_mart_par} — reentrada ${nuevo_monto} "
                                f"excede exposición máxima ${config.get('martingala_max_exposicion_usd', 0):.2f}. "
                                f"Reset a monto base.")
                            monto_actual_par = _calcular_monto_base(par)
                            nivel_mart_par   = 0
                            mart_pendiente   = False
                        else:
                            monto_actual_par = limitado
                            mart_pendiente   = True
                            mart_dir_iq      = "call" if mart_senal == "BUY" else "put"
                            log(f"[MART] {par} nivel {nivel_mart_par} — nueva reentrada en próxima vela × ${monto_actual_par}")
                    else:
                        # Niveles agotados → reset
                        monto_actual_par = _calcular_monto_base(par)
                        nivel_mart_par   = 0
                        log(f"[MART] {par} niveles agotados — reset a ${monto_actual_par}")

                    with _lock:
                        estado["losses"]            += 1
                        estado["perdidas_seguidas"]  = perdidas_seguidas_par
                        estado["martingala_nivel"]   = nivel_mart_par
                        estado["martingala_monto"]   = monto_actual_par
                    log(f"[LOSS-MART] ❌ {par} ${ganancia:.2f} | Seguidas: {perdidas_seguidas_par}/{config['par_perdidas_max']} | Día: {estado['losses']}/{config['max_perdidas_dia']} | Bal: ${balance:.2f}")
                    enviar_telegram(
                        f"💔 <b>MARTINGALA PERDIDA ❌</b>\n"
                        f"Par: <b>{par}</b>\n"
                        f"Dirección: {dir_txt}\n"
                        f"Pérdida: <b>${ganancia:.2f}</b>\n"
                        f"Balance: <b>${balance:.2f}</b>\n"
                        f"Seguidas en {par}: {perdidas_seguidas_par}/{config['par_perdidas_max']} | Día: {estado['losses']}/{config['max_perdidas_dia']}"
                    )
                    op = {"par": par, "direccion": mart_senal, "monto": monto_orden,
                          "resultado": "loss", "ganancia": round(ganancia, 2),
                          "balance": round(balance, 2), "fecha": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                          "estrategia": config.get("estrategia", ""),
                          "racha_perdidas_momento": perdidas_seguidas_par}
                    with _lock:
                        estado["operaciones"].insert(0, op)

                    if perdidas_seguidas_par >= config.get("par_perdidas_max", 3):
                        bloquear_par(par)
                        perdidas_seguidas_par = 0
                        monto_actual_par = _calcular_monto_base(par)
                        nivel_mart_par   = 0
                        mart_pendiente   = False
                        break

                else:
                    perdidas_seguidas_par = 0
                    with _lock:
                        estado["perdidas_seguidas"] = 0
                    log(f"[DRAW-MART] {par} empate | Bal: ${balance:.2f}")

                # Guardar sesión persistente tras cada resultado cerrado
                # (win/loss/empate). Así un Detener + Iniciar rápido continúa
                # exactamente donde estaba.
                guardar_sesion()
                # Registro permanente en CSV (historial externo para Excel)
                try:
                    if estado["operaciones"]:
                        registrar_operacion_historial(estado["operaciones"][0])
                except Exception:
                    pass

                # Verificar stops globales
                motivo = verificar_stop(perdidas_seguidas_par)
                if motivo:
                    _aplicar_stop(motivo, perdidas_seguidas_par, balance)
                    break
                continue   # volver al inicio del while

            # ══════════════════════════════════════════════════════════════
            # FLUJO NORMAL: buscar señal del MACD
            # ══════════════════════════════════════════════════════════════
            senal, razon = analizar(velas, estado_senal_par, par)
            log(f"[{ahora.strftime('%H:%M:%S')}] {par} → {razon}")

            with _lock:
                estado["pares_estado"][par] = {"senal": senal if senal != "NONE" else "-"}

            if senal not in ("BUY", "SELL"):
                continue

            _estrategia_activa = config.get("estrategia", "ifc")

            # ── Filtro de payout (regla del video de Facundo) ──────────
            # Solo operar pares cuyo payout actual >= facundo_payout_min.
            # El cache lo refresca loop_bot fuera de esta ruta caliente.
            if _estrategia_activa == "facundo":
                _pok, _pval = _facundo_payout_ok(par)
                if not _pok:
                    log(f"[FACUNDO] {par} señal descartada: payout {_pval}% < "
                        f"mínimo {config.get('facundo_payout_min')}% "
                        f"(facundo_payout_min en config.json)")
                    continue

            dir_txt = "🟢 COMPRA (CALL)" if senal == "BUY" else "🔴 VENTA (PUT)"
            log(f"[SEÑAL] {par} {dir_txt}")

            # ── Candidatura a la entrada de esta vela ─────────────────
            # Prioridad medible de la señal; si otro par también señala en
            # esta misma vela, al segundo 00 solo entrará el mejor.
            # El detector de prioridad debe ser el de la estrategia ACTIVA
            # (antes se usaba siempre detectar_ifc, así que con Facundo la
            # prioridad caía a un valor plano y el desempate era ciego).
            if _estrategia_activa == "facundo":
                _s_prio, det_prio, _rz_prio = _facundo_decidir(velas)
            elif _estrategia_activa == "ifcpro":
                det_prio = detectar_ifcpro(velas)
            else:
                det_prio = detectar_ifc(velas)
            prioridad, desglose = _prioridad_senal(velas, det_prio or {})
            _registrar_candidato(minuto_analisis, par, prioridad)
            log(f"[PRIORIDAD] {par} candidato con prioridad {prioridad:.0f} ({desglose}).")

            enviar_telegram(
                f"⚡ <b>Señal detectada</b>\n"
                f"Par: <b>{par}</b>\n"
                f"Dirección: <b>{dir_txt}</b>\n"
                f"Patrón: <b>{razon}</b>\n"
                f"Esperando apertura de vela..."
            )

            # ── ENTRADA: segundo 00 exacto, con tolerancia configurable ──
            # Caso normal: el análisis (al :57) termina antes de :59 y se
            # espera al segundo 00 exacto. Si por un instante lento el
            # análisis cae apenas pasado el 00, se permite entrar hasta
            # entrada_tolerancia_seg en vez de perder la señal. Más allá
            # de eso, o en otra vela, se descarta.
            tolerancia = float(config.get("entrada_tolerancia_seg", 2))
            min_ahora = int(time.time() // 60)
            if min_ahora == minuto_analisis:
                wait_secs = segundos_para_nueva_vela()
                log(f"[TIMING] {par} — esperando {wait_secs:.1f}s al segundo 00...")
                esperar_apertura_vela()
                min_ahora = int(time.time() // 60)

            seg_entrada = time.time() % 60
            if min_ahora != minuto_analisis + 1 or seg_entrada > tolerancia:
                log(f"[SKIP] {par} — fuera de ventana de entrada (vela +{min_ahora - minuto_analisis}, "
                    f"seg {seg_entrada:.1f}, tolerancia {tolerancia:.0f}s). Señal descartada.")
                continue
            if seg_entrada > 0.5:
                log(f"[TIMING] {par} — entrada con tolerancia en el segundo {seg_entrada:.1f}.")

            # ── Solo entra el mejor: si hubo 2+ señales en esta vela, el
            # primero en llegar al segundo 00 congela al ganador (mayor
            # prioridad) y los demás ceden — una sola compra, exacta. ──
            gano_prio, ganador, cands = _soy_ganador(minuto_analisis, par)
            if not gano_prio:
                detalle = ", ".join(f"{p}:{v:.0f}" for p, v in sorted(
                    (cands or {}).items(), key=lambda x: -x[1]))
                log(f"[PRIORIDAD] {par} cede esta vela a {ganador} ({detalle}).")
                continue
            if cands and len(cands) > 1:
                rivales = ", ".join(f"{p}:{v:.0f}" for p, v in sorted(
                    cands.items(), key=lambda x: -x[1]) if p != par)
                log(f"[PRIORIDAD] {par} GANA la vela (prioridad {cands.get(par, 0):.0f}) frente a: {rivales}.")

            dir_iq = "call" if senal == "BUY" else "put"

            if config.get("martingala_activa", False):
                monto_orden = round(monto_actual_par, 2)
            else:
                monto_orden = config["monto"]
                monto_actual_par = monto_orden
                nivel_mart_par   = 0

            with _lock:
                estado["martingala_nivel"] = nivel_mart_par
                estado["martingala_monto"] = monto_orden

            # AJUSTE v9.1.1: Aplicar límite de exposición total (exposicion_max_pct).
            # Antes este campo estaba declarado pero no se aplicaba (zombie).
            # Ahora: si la suma de montos en vuelo + nuevo monto > balance * pct/100,
            # se descarta la señal con log y se continúa al siguiente ciclo.
            exp_max_pct = float(config.get("exposicion_max_pct", 0) or 0)
            if exp_max_pct > 0:
                with _lock:
                    balance_actual = float(estado.get("balance", 0.0) or 0.0)
                    en_vuelo = float(estado.get("exposicion_en_vuelo", 0.0) or 0.0)
                limite_usd = balance_actual * (exp_max_pct / 100.0)
                if balance_actual > 0 and (en_vuelo + monto_orden) > limite_usd:
                    log(f"[EXPOSICION] {par} señal descartada — en vuelo ${en_vuelo:.2f} + "
                        f"nuevo ${monto_orden:.2f} = ${en_vuelo + monto_orden:.2f} excede "
                        f"límite ${limite_usd:.2f} ({exp_max_pct}% del balance ${balance_actual:.2f}).")
                    continue

            # AJUSTE v9.1.1: registrar monto como "en vuelo" antes de ejecutar.
            with _lock:
                estado["exposicion_en_vuelo"] = float(estado.get("exposicion_en_vuelo", 0.0)) + monto_orden

            ganancia, balance = _ejecutar_orden(par, dir_iq, senal, monto_orden, expiracion, ahora, idx)

            # AJUSTE v9.1.1: liberar el monto en vuelo (la orden ya cerró).
            with _lock:
                estado["exposicion_en_vuelo"] = max(0.0, float(estado.get("exposicion_en_vuelo", 0.0)) - monto_orden)

            if ganancia is None:
                continue

            if ganancia > 0:
                perdidas_seguidas_par = 0
                monto_actual_par = _calcular_monto_base(par)
                nivel_mart_par   = 0
                with _lock:
                    estado["wins"]             += 1
                    estado["perdidas_seguidas"] = 0
                    estado["martingala_nivel"]  = 0
                    estado["martingala_monto"]  = config["monto"]
                log(f"[WIN] ✅ {par} +${ganancia:.2f} | Wins hoy: {estado['wins']}/{config['objetivo_dia']} | Bal: ${balance:.2f}")
                enviar_telegram(
                    f"🏆 <b>GANADA ✅</b>\n"
                    f"Par: <b>{par}</b>\n"
                    f"Dirección: {dir_txt}\n"
                    f"Ganancia: <b>+${ganancia:.2f}</b>\n"
                    f"Balance: <b>${balance:.2f}</b>\n"
                    f"Wins hoy: {estado['wins']}/{config['objetivo_dia']}"
                )
                op = {"par": par, "direccion": senal, "monto": monto_orden,
                      "resultado": "win", "ganancia": round(ganancia, 2),
                      "balance": round(balance, 2), "fecha": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                          "estrategia": config.get("estrategia", ""),
                          "racha_perdidas_momento": perdidas_seguidas_par}
                with _lock:
                    estado["operaciones"].insert(0, op)

            elif ganancia < 0:
                perdidas_seguidas_par += 1

                if config.get("martingala_activa", False):
                    if nivel_mart_par < config.get("martingala_niveles", 1):
                        # Subir nivel y programar reentrada inmediata en la próxima vela
                        nivel_mart_par  += 1
                        nuevo_monto = round(monto_actual_par * config.get("martingala_mult", 2.0), 2)
                        # SAFETY: limitar la exposición total de la racha.
                        monto_acumulado = sum([_calcular_monto_base(par)] + [
                            _calcular_monto_base(par) * (config.get("martingala_mult", 2.0) ** i)
                            for i in range(nivel_mart_par - 1)
                        ])
                        limitado = _limitar_martingala(monto_acumulado, nuevo_monto)
                        if limitado is None:
                            log(f"[MART-LIMITE] {par} nivel {nivel_mart_par} — reentrada ${nuevo_monto} "
                                f"excede exposición máxima ${config.get('martingala_max_exposicion_usd', 0):.2f}. "
                                f"Reset a monto base.")
                            monto_actual_par = _calcular_monto_base(par)
                            nivel_mart_par   = 0
                            mart_pendiente   = False
                        else:
                            monto_actual_par = limitado
                            mart_pendiente   = True
                            mart_dir_iq      = dir_iq       # misma dirección
                            mart_senal       = senal
                            log(f"[MART] {par} nivel {nivel_mart_par} — reentrada automática en próxima vela × ${monto_actual_par}")
                    else:
                        monto_actual_par = _calcular_monto_base(par)
                        nivel_mart_par   = 0
                        log(f"[MART] {par} niveles agotados — reset a ${monto_actual_par}")

                with _lock:
                    estado["losses"]            += 1
                    estado["perdidas_seguidas"]  = perdidas_seguidas_par
                    estado["martingala_nivel"]   = nivel_mart_par
                    estado["martingala_monto"]   = monto_actual_par
                log(f"[LOSS] ❌ {par} ${ganancia:.2f} | Seguidas: {perdidas_seguidas_par}/{config['par_perdidas_max']} | Día: {estado['losses']}/{config['max_perdidas_dia']} | Bal: ${balance:.2f}")
                enviar_telegram(
                    f"💔 <b>PERDIDA ❌</b>\n"
                    f"Par: <b>{par}</b>\n"
                    f"Dirección: {dir_txt}\n"
                    f"Pérdida: <b>${ganancia:.2f}</b>\n"
                    f"Balance: <b>${balance:.2f}</b>\n"
                    f"Seguidas en {par}: {perdidas_seguidas_par}/{config['par_perdidas_max']} | Día: {estado['losses']}/{config['max_perdidas_dia']}"
                )
                op = {"par": par, "direccion": senal, "monto": monto_orden,
                      "resultado": "loss", "ganancia": round(ganancia, 2),
                      "balance": round(balance, 2), "fecha": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                          "estrategia": config.get("estrategia", ""),
                          "racha_perdidas_momento": perdidas_seguidas_par}
                with _lock:
                    estado["operaciones"].insert(0, op)

                if perdidas_seguidas_par >= config.get("par_perdidas_max", 3):
                    bloquear_par(par)
                    perdidas_seguidas_par = 0
                    monto_actual_par = _calcular_monto_base(par)
                    nivel_mart_par   = 0
                    mart_pendiente   = False
                    break

            else:
                perdidas_seguidas_par = 0
                with _lock:
                    estado["perdidas_seguidas"] = 0
                log(f"[DRAW] {par} empate | Bal: ${balance:.2f}")

            # Guardar sesión persistente tras cada resultado cerrado
            # (win/loss/empate). Así un Detener + Iniciar rápido continúa
            # exactamente donde estaba.
            guardar_sesion()
            # Registro permanente en CSV (historial externo para Excel)
            try:
                if estado["operaciones"]:
                    registrar_operacion_historial(estado["operaciones"][0])
            except Exception:
                pass

            # Verificar stops globales
            motivo = verificar_stop(perdidas_seguidas_par)
            if motivo:
                _aplicar_stop(motivo, perdidas_seguidas_par, balance)
                break

        except Exception:
            log(f"[ERROR] {par} — " + traceback.format_exc())
            time.sleep(5)
        # El heartbeat se actualiza al INICIO de cada iteración (no aquí
        # al final), para que los `continue` no lo salten.

    # Al salir del hilo, quitamos el heartbeat para que el watchdog no
    # lo siga marcando como "atascado" cuando ya no opera.
    _heartbeat_quitar(par)
    log(f"[{par}] Hilo detenido.")


def _aplicar_stop(motivo, perdidas_seguidas, balance):
    """Notifica y detiene el bot según el motivo de stop."""
    if motivo == "stop_objetivo":
        log(f"[STOP] 🎯 Objetivo del día alcanzado: {estado['wins']} wins. Bot detenido.")
        enviar_telegram(
            f"🎯 <b>OBJETIVO ALCANZADO</b>\n"
            f"Wins del día: <b>{estado['wins']}</b>\n"
            f"El bot se detuvo automáticamente.\n"
            f"Balance: <b>${balance:.2f}</b>"
        )
    elif motivo == "stop_dia":
        log(f"[STOP] ⛔ Máximo de pérdidas del día: {estado['losses']}. Bot detenido.")
        enviar_telegram(
            f"⛔ <b>STOP DEL DÍA</b>\n"
            f"Pérdidas del día: <b>{estado['losses']}</b>\n"
            f"El bot se detuvo automáticamente.\n"
            f"Balance: <b>${balance:.2f}</b>"
        )
    elif motivo == "stop_seguidas":
        log(f"[STOP] ⚠️ {perdidas_seguidas} pérdidas seguidas globales. Bot detenido.")
        enviar_telegram(
            f"⚠️ <b>STOP POR PÉRDIDAS SEGUIDAS</b>\n"
            f"Pérdidas consecutivas: <b>{perdidas_seguidas}</b>\n"
            f"El bot se detuvo automáticamente.\n"
            f"Balance: <b>${balance:.2f}</b>"
        )
    with _lock:
        estado["corriendo"] = False


# =============================================================
# LOOP PRINCIPAL (conexión + lanzador de hilos por par)
# =============================================================

def cargar_instrumentos_iq():
    """Fuerza que la librería cargue la lista de instrumentos que
    get_all_open_time() necesita. Sin esto, get_all_open_time() suele
    lanzar 'NoneType object is not subscriptable' (el error que veíamos).
    Cada método se protege con timeout y se ignoran los que no existan
    en esta versión de la librería."""
    if not _Iq:
        return
    for metodo in ("update_ACTIVES_OPCODE", "get_all_init_v2", "get_all_init"):
        fn = getattr(_Iq, metodo, None)
        if fn:
            _con_timeout(fn, 15)   # solo dispara la carga; ignora resultado/errores


def _tabla_opcode():
    """Devuelve, POR REFERENCIA, el dict ACTIVES (nombre -> id) que usan
    get_candles()/buy() de la librería, o None si no se puede acceder.
    Mutar este dict hace que la librería reconozca nuevos pares."""
    if not _Iq:
        return None
    try:
        t = _Iq.get_all_ACTIVES_OPCODE()      # en el fork Lu-Yi-Hsun devuelve el dict real
        if isinstance(t, dict):
            return t
    except Exception:
        pass
    try:
        import importlib
        mod = importlib.import_module("iqoptionapi.constants")
        if hasattr(mod, "ACTIVES") and isinstance(mod.ACTIVES, dict):
            return mod.ACTIVES
    except Exception:
        pass
    return None


def _buscar_id_activo(nombre, por_nombre):
    """Busca el id de un activo por nombre, tolerando mayúsculas/guion/guion
    bajo (p.ej. 'EURUSD-OTC' vs 'EURUSDOTC')."""
    info = por_nombre.get(nombre)
    if info and info.get("id") is not None:
        return info["id"]
    objetivo = nombre.upper().replace("-", "").replace("_", "")
    for k, info in por_nombre.items():
        if info.get("id") is not None and k.upper().replace("-", "").replace("_", "") == objetivo:
            return info["id"]
    return None


def registrar_activos_operables(pares_extra=None):
    """Registra en la tabla de la librería (OP_code.ACTIVES) TODOS los
    activos que el servidor reporta, con su id numérico. Así get_candles()
    y buy() reconocen CUALQUIER par por su nombre, no solo la lista corta
    que la librería precarga — que es la causa de que 'solo funcionen los
    primeros pares'. Además garantiza que los pares pedidos queden mapeados
    aunque el nombre venga con alguna variante. Devuelve (n_registrados,
    por_nombre)."""
    por_nombre, hubo_fallo = obtener_estado_binarios()
    if hubo_fallo or not por_nombre:
        log("[ACTIVOS] No pude leer la lista de activos del servidor (get_all_init_v2). "
            "Los pares fuera de la lista precargada podrían no reconocerse todavía.")
        return 0, {}
    tabla = _tabla_opcode()
    if tabla is None:
        log("[ACTIVOS] No pude acceder a la tabla OP_code.ACTIVES de la librería.")
        return 0, por_nombre
    n = 0
    # 1) Registrar TODOS los activos del servidor por su nombre exacto.
    for nombre, info in por_nombre.items():
        iid = info.get("id")
        if iid is not None:
            try:
                tabla[nombre] = int(iid)
                n += 1
            except Exception:
                pass
    # 2) Asegurar que los pares pedidos queden mapeados (tolerando variantes).
    faltantes = []
    for par in (pares_extra or []):
        if par not in tabla:
            iid = _buscar_id_activo(par, por_nombre)
            if iid is not None:
                try:
                    tabla[par] = int(iid)
                except Exception:
                    pass
            else:
                faltantes.append(par)
    log(f"[ACTIVOS] {n} activos registrados como operables (cualquier par seleccionable ahora).")
    if faltantes:
        log(f"[ACTIVOS] Ojo: no encontré en el servidor estos pares pedidos: {', '.join(faltantes)} "
            f"(¿nombre mal escrito o mercado cerrado?).")
    return n, por_nombre


def get_all_open_time_robusto(intentos=4, espera=1.2):
    """Devuelve (open_time, error_str). Antes de cada intento fuerza la
    carga de instrumentos, lo que evita el 'NoneType' típico. Reintenta
    porque a veces la primera respuesta llega vacía o incompleta."""
    if not _Iq:
        return None, "sin conexión"
    ultimo = "desconocido"
    for _ in range(intentos):
        cargar_instrumentos_iq()
        time.sleep(espera)
        completo, ot, exc = _con_timeout(_Iq.get_all_open_time, 15)
        if not completo:
            ultimo = "get_all_open_time() no respondió (timeout)"
            continue
        if exc is not None:
            ultimo = str(exc)
            continue
        if ot:
            return ot, None
        ultimo = "get_all_open_time() devolvió vacío"
    return None, ultimo


def obtener_estado_binarios(intentos=3, espera=1.0):
    """Devuelve (por_nombre, hubo_fallo).

    por_nombre = { "EURUSD-op": {"id": 1, "binary": bool, "turbo": bool}, ... }

    Fuente FIABLE (get_all_init_v2) para binarias/turbo. El KEY del dict de
    'actives' es el id numérico del activo; el nombre visible puede traer
    sufijos como '-op' (mercado normal) o '-OTC'. Guardamos nombre + id.
    """
    if not _Iq:
        return {}, True
    fn = getattr(_Iq, "get_all_init_v2", None)
    if fn is None:
        return {}, True

    data = None
    for _ in range(intentos):
        completo, res, exc = _con_timeout(fn, 35)
        if completo and exc is None and res and ("binary" in res or "turbo" in res):
            data = res
            break
        time.sleep(espera)
    if not data:
        return {}, True

    por_nombre = {}
    for option in ("binary", "turbo"):
        bloque  = data.get(option) if isinstance(data, dict) else None
        actives = bloque.get("actives") if isinstance(bloque, dict) else None
        if isinstance(actives, dict):
            for aid, active in actives.items():
                try:
                    iid = int(aid)
                except Exception:
                    iid = None
                nombre = str(active.get("name", "")).split(".")[-1].strip()
                if not nombre:
                    continue
                abierto = bool(active.get("enabled", False)) and not bool(active.get("is_suspended", False))
                e = por_nombre.setdefault(nombre, {"id": iid})
                e[option] = abierto
                if iid is not None:
                    e["id"] = iid
    return por_nombre, False


# =============================================================
# ESCÁNER DE PARES (integrado)
# Puntúa la calidad de tendencia de cada par y elige los mejores
# para operar. Portado del OTC Scanner, usando los indicadores
# que el bot ya trae (ADX, EMAs, RSI).
# =============================================================
def _atr_promedio(velas, periodo=14):
    """ATR simple: promedio de los últimos 'periodo' true ranges."""
    if len(velas) < periodo + 1:
        return None
    trs = []
    for i in range(1, len(velas)):
        h, l, pc = velas[i]["max"], velas[i]["min"], velas[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < periodo:
        return None
    return sum(trs[-periodo:]) / periodo


def puntuar_par(velas):
    """Puntúa la calidad de tendencia (0-100) de un par — mismos criterios
    del escáner:
      ADX>=25 +20 y ADX>=35 +10 extra (tendencia real, no lateral)
      EMA9 vs EMA21 +15 (alineación); separación >= 0.0003 +5
      Pendiente de la EMA50 a favor +15 (tendencia de fondo)
      RSI 35-65 +15 (momentum disponible); saliendo de extremos +8
      Volatilidad: rango de la última vela > 0.7*ATR +15
      Último cierre alineado con la dirección +5
    Devuelve (score, detalles, direccion BUY/SELL/LATERAL/NEUTRAL, rating)."""
    if len(velas) < 60:
        return 0, {}, "NEUTRAL", "Pocas velas"
    closes = [v["close"] for v in velas]
    opens  = [v["open"]  for v in velas]
    score, detalles = 0, {}

    adx_val = calcular_adx(velas, 14)
    detalles["adx"] = round(adx_val, 1) if adx_val is not None else None
    if adx_val is not None:
        if adx_val >= 25:
            score += 20
        if adx_val >= 35:
            score += 10

    ema9  = calcular_ema(closes, 9)
    ema21 = calcular_ema(closes, 21)
    ema_dir = "NEUTRAL"
    if ema9 and ema21:
        sep = abs(ema9 - ema21)
        detalles["sep_ema"] = round(sep, 5)
        ema_dir = "BUY" if ema9 > ema21 else "SELL"
        score += 15
        if sep >= 0.0003:
            score += 5

    ema50_s = calcular_ema_series(closes, 50)
    if ema50_s and len(ema50_s) >= 6:
        slope = ema50_s[-1] - ema50_s[-6]
        detalles["ema50_slope"] = round(slope, 6)
        if (ema_dir == "BUY" and slope > 0) or (ema_dir == "SELL" and slope < 0):
            score += 15

    rsi_val = calcular_rsi(velas, 14)
    detalles["rsi"] = round(rsi_val, 1) if rsi_val is not None else None
    if rsi_val is not None:
        if 35 <= rsi_val <= 65:
            score += 15
        elif 25 <= rsi_val < 35 and ema_dir == "BUY":
            score += 8
        elif 65 < rsi_val <= 75 and ema_dir == "SELL":
            score += 8

    atr_val = _atr_promedio(velas, 14)
    detalles["atr"] = round(atr_val, 5) if atr_val else None
    if atr_val and (velas[-1]["max"] - velas[-1]["min"]) > atr_val * 0.7:
        score += 15

    if ema_dir == "BUY" and closes[-1] > opens[-1]:
        score += 5
    if ema_dir == "SELL" and closes[-1] < opens[-1]:
        score += 5

    direccion = ema_dir
    if adx_val is not None and adx_val < 20:
        direccion = "LATERAL"

    if score >= 75:
        rating = "EXCELENTE"
    elif score >= 55:
        rating = "BUENO"
    elif score >= 35:
        rating = "REGULAR"
    else:
        rating = "EVITAR"
    return min(score, 100), detalles, direccion, rating


def escanear_y_elegir_pares(candidatos, top_n=4):
    """Escanea los pares candidatos con velas M1 reales, los puntúa con
    puntuar_par() y devuelve (elegidos, ranking). Cada get_candles va con
    timeout — ningún par puede colgar el escaneo (misma protección que el
    resto del bot). Publica el ranking en estado['scan'] y en el log."""
    velas_scan = int(config.get("scanner_velas", 100))
    ranking = []
    total = len(candidatos)
    log(f"[SCAN] Escaneando {total} pares para elegir los {top_n} mejores...")
    for i, par in enumerate(candidatos):
        if not estado.get("corriendo", False):
            log("[SCAN] Escaneo cancelado (bot detenido).")
            return [], ranking
        # Ventana sagrada alrededor del segundo 00: no tomar el lock ahí,
        # para no retrasar las COMPRAS de los hilos (entrada exacta al :00).
        s_now = datetime.datetime.utcnow().second
        if s_now >= 57 or s_now < 2:
            time.sleep(((2 - s_now) % 60) or 1)
        try:
            with _buy_lock:
                completo, velas, exc = _con_timeout(
                    _Iq.get_candles, 15, par, 60, velas_scan, time.time())
        except Exception as e:
            log(f"[SCAN] {par}: error ({e}), se omite")
            continue
        if not completo:
            log(f"[SCAN] {par}: sin respuesta (timeout 15s), se omite")
            continue
        if exc is not None or not velas or len(velas) < 60:
            log(f"[SCAN] {par}: sin datos suficientes, se omite")
            continue
        score, det, direccion, rating = puntuar_par(velas)
        ranking.append({"par": par, "score": score, "direccion": direccion,
                        "rating": rating, "adx": det.get("adx"), "rsi": det.get("rsi")})
        log(f"[SCAN] ({i+1}/{total}) {par}: score={score} | {direccion} | {rating} | ADX={det.get('adx')}")
        time.sleep(0.3)   # pausa corta para no saturar la API
    ranking.sort(key=lambda x: x["score"], reverse=True)
    elegidos = [r["par"] for r in ranking[:top_n]]
    with _lock:
        estado["scan"] = {
            "cuando":   time.strftime("%H:%M:%S"),
            "ranking":  ranking,
            "elegidos": elegidos,
        }
    if elegidos:
        log("[SCAN] ✅ Mejores pares: " + ", ".join(
            f"{r['par']} ({r['score']})" for r in ranking[:top_n]))
    return elegidos, ranking


def _decidir_rotacion(ranking, operando, score_min):
    """A partir del ranking de un re-escaneo, decide qué pares operando
    SALEN (quedaron LATERAL, sin tendencia) y qué candidatos ENTRAN en su
    lugar (tendencia clara BUY/SELL, score >= score_min y mayor que el del
    saliente). Si no hay reemplazo que valga la pena, el par se queda —
    nunca se cambia lateral por lateral. Devuelve lista de (sale, entra)."""
    por_par = {r["par"]: r for r in ranking}
    disponibles = [r for r in ranking
                   if r["par"] not in operando
                   and r["direccion"] in ("BUY", "SELL")
                   and r["score"] >= score_min]
    cambios, usados = [], set()
    for par in operando:
        info = por_par.get(par)
        if info is None or info["direccion"] != "LATERAL":
            continue   # no escaneable o sigue con tendencia -> se queda
        for cand in disponibles:
            if cand["par"] in usados:
                continue
            if cand["score"] > info["score"]:
                cambios.append((info, cand))
                usados.add(cand["par"])
                break
    return cambios


def calibrar_pares(candidatos):
    """AUTO-CALIBRACIÓN: para cada par candidato trae histórico REAL del
    bróker y corre el backtest IFC en ambos modos. Selecciona los
    autocal_max_pares mejores cuyo mejor modo supere autocal_umbral, y
    asigna a cada par SU modo ganador (así GBPCAD puede operar continuidad
    y GBPCHF reversión a la vez). Publica la tabla en estado['autocal'].
    Devuelve (seleccion, tabla) con seleccion = [(par, modo, winrate)]."""
    velas_n  = int(config.get("autocal_velas", 4000))
    umbral   = float(config.get("autocal_umbral", 54.0))
    max_p    = max(1, int(config.get("autocal_max_pares", 4)))
    usar_ema = config.get("ifc_usar_ema", True)
    tabla = []
    total = len(candidatos)
    log(f"[AUTOCAL] Calibrando {total} pares con {velas_n} velas reales cada uno "
        f"(umbral {umbral}%). Esto toma un par de minutos...")
    for i, par in enumerate(candidatos):
        if not estado.get("corriendo", False):
            log("[AUTOCAL] Calibración cancelada (bot detenido).")
            return [], tabla
        if par in _lista_negra:
            continue
        velas = _fetch_historico(par, velas_n)
        if not velas or len(velas) < 500:
            log(f"[AUTOCAL] ({i+1}/{total}) {par}: sin histórico suficiente, se omite")
            continue
        c = _backtest_sobre_velas(velas, "continuidad", usar_ema)
        r = _backtest_sobre_velas(velas, "reversion",   usar_ema)
        if c["total"] < 30:
            log(f"[AUTOCAL] ({i+1}/{total}) {par}: muy pocas señales ({c['total']}), se omite")
            continue
        if c["winrate"] >= r["winrate"]:
            mejor_modo, wr = "continuidad", c["winrate"]
        else:
            mejor_modo, wr = "reversion", r["winrate"]

        # ── VALIDACIÓN DOBLE: el borde debe existir en AMBAS mitades del
        # histórico. Filtra a los "ganadores con suerte" de una ventana. ──
        valido = True
        mitades_txt = ""
        if config.get("autocal_validacion_doble", True):
            mitad = len(velas) // 2
            u_mitad = float(config.get("autocal_umbral_mitad", 52.0))
            a = _backtest_sobre_velas(velas[:mitad], mejor_modo, usar_ema)
            b = _backtest_sobre_velas(velas[mitad:], mejor_modo, usar_ema)
            valido = (a["total"] >= 10 and b["total"] >= 10 and
                      a["winrate"] >= u_mitad and b["winrate"] >= u_mitad)
            mitades_txt = f" | mitades {a['winrate']}%/{b['winrate']}%"

        tabla.append({"par": par, "modo": mejor_modo, "winrate": wr,
                      "ops": c["total"], "cont": c["winrate"], "rev": r["winrate"],
                      "valido": valido})
        if wr >= umbral and valido:
            marca = "  ✔ SUPERA UMBRAL"
        elif wr >= umbral:
            marca = "  ✗ descartado: no consistente entre mitades"
        else:
            marca = ""
        log(f"[AUTOCAL] ({i+1}/{total}) {par}: continuidad {c['winrate']}% | "
            f"reversión {r['winrate']}% | {c['total']} ops -> mejor: {mejor_modo} {wr}%{mitades_txt}{marca}")
    tabla.sort(key=lambda x: x["winrate"], reverse=True)
    seleccion = [(t["par"], t["modo"], t["winrate"])
                 for t in tabla if t["winrate"] >= umbral and t.get("valido", True)][:max_p]
    with _lock:
        estado["autocal"] = {
            "cuando": time.strftime("%H:%M:%S"), "umbral": umbral,
            "tabla": tabla,
            "seleccion": [{"par": p, "modo": m, "winrate": w} for p, m, w in seleccion],
        }
    return seleccion, tabla


def _revisar_lista_negra():
    """Revisa el desempeño EN VIVO de la sesión y banquea (por el resto
    de la sesión) a los pares con lista_negra_min_ops o más operaciones y
    winrate <= lista_negra_max_wr. El hilo del par termina su operación
    en curso y se apaga solo (mismo mecanismo seguro de la rotación).
    Devuelve la lista de pares recién banqueados."""
    if not config.get("lista_negra_activa", False):
        return []
    min_ops = int(config.get("lista_negra_min_ops", 8))
    max_wr  = float(config.get("lista_negra_max_wr", 40.0))
    try:
        stats = calcular_desempeno_por_par()
    except Exception:
        return []
    nuevos = []
    for par, r in (stats or {}).items():
        if par in _lista_negra:
            continue
        if r.get("total", 0) >= min_ops and r.get("winrate", 100.0) <= max_wr:
            _lista_negra.add(par)
            _pares_desactivados.add(par)
            nuevos.append(par)
            log(f"[LISTA-NEGRA] {par} banqueado por la sesión: {r['wins']}W/{r['losses']}L "
                f"({r['winrate']}% en {r['total']} ops). Su hilo termina la operación en curso y se apaga.")
            enviar_telegram(f"🚫 <b>Lista negra</b>\n{par}: {r['winrate']}% en {r['total']} ops — "
                            f"banqueado por el resto de la sesión")
    return nuevos


def _racha_perdidas_par(par, operaciones=None):
    """Pérdidas SEGUIDAS más recientes de 'par' en la sesión (se corta en
    la última ganada). Se calcula del historial real de operaciones."""
    if operaciones is None:
        with _lock:
            operaciones = list(estado["operaciones"])
    racha = 0
    for op in reversed(operaciones):
        if op.get("par") != par:
            continue
        if op.get("resultado") == "win":
            break
        racha += 1
    return racha


def _elegir_sustituto(pares_actuales):
    """Siguiente mejor par DISPONIBLE para entrar en caliente:
    1) de la tabla de calibración (mayor winrate primero; prioridad a los
       que pasaron la validación doble), con su modo propio;
    2) si no hubo calibración, del ranking del escáner (mayor score).
    Excluye los que ya operan, los apagados y la lista negra.
    Devuelve (par, modo|None, winrate/score) o None."""
    ocupados = set(p for p in pares_actuales if p not in _pares_desactivados)
    with _lock:
        tabla   = list((estado.get("autocal") or {}).get("tabla") or [])
        ranking = list((estado.get("scan") or {}).get("ranking") or [])

    def _disp(par):
        return (par and par not in ocupados
                and par not in _pares_desactivados and par not in _lista_negra)

    if tabla:
        orden = sorted(tabla, key=lambda x: x.get("winrate", 0), reverse=True)
        for solo_validos in (True, False):
            for t in orden:
                if solo_validos and not t.get("valido", True):
                    continue
                if _disp(t.get("par")):
                    return (t["par"], t.get("modo"), t.get("winrate"))
        return None
    for r in sorted(ranking, key=lambda x: x.get("score", 0), reverse=True):
        if _disp(r.get("par")):
            return (r["par"], None, r.get("score"))
    return None


def obtener_pares_normales_abiertos(pares_deseados):
    """
    Consulta a IQ Option cuáles de los 'pares_deseados' (forex normal,
    sin sufijo -OTC) están actualmente abiertos para operar.

    Devuelve (abiertos: list, hubo_fallo: bool). 'hubo_fallo' distingue
    "no se pudo consultar" (conexión inestable, timeout, etc.) de
    "se consultó bien y ningún par deseado está abierto ahora" — esto
    permite que loop_bot aplique un backoff más largo entre intentos
    cuando la conexión está fallando repetidamente, en vez de insistir
    cada pocos minutos sin ninguna esperanza real de éxito.

    NOTA DE IMPLEMENTACIÓN: la librería iqoptionapi (no oficial) expone
    esto típicamente mediante _Iq.get_all_open_time(), que devuelve un
    dict anidado del tipo {"forex": {"EURUSD": {"open": True/False}, ...},
    "binary": {...}, "turbo": {...}}. Si tu versión de la librería usa
    un nombre o estructura distinta, este bloque lo reporta en el log
    en vez de romper el bot — revisa ese mensaje y ajusta la línea
    marcada abajo si hace falta, según lo que veas exactamente ahí.
    """
    if not _Iq:
        return [], True

    # Fuente FIABLE (get_all_init_v2). Ver obtener_estado_binarios().
    por_nombre, hubo_fallo = obtener_estado_binarios()
    if hubo_fallo:
        log("[PARES-NORMALES] No pude leer el estado de instrumentos (get_all_init_v2 no respondió).")
        return [], True

    # IMPORTANTE: en muchas cuentas el mercado normal se nombra con sufijo
    # '-op' (EURUSD-op, GBPUSD-op...), no "EURUSD" a secas. Registramos los
    # nombres del servidor en la tabla que usa buy()/get_candles() para poder
    # operarlos por su nombre real.
    cargar_instrumentos_iq()
    try:
        conocidos = _Iq.get_all_ACTIVES_OPCODE() or {}
    except Exception:
        conocidos = {}

    abiertos = []
    for par in pares_deseados:
        elegido = None
        estaba_abierto = False
        # aceptamos el nombre pedido y su variante "-op"
        for cand in (par, par + "-op"):
            info = por_nombre.get(cand)
            if info and (info.get("binary") or info.get("turbo")):
                estaba_abierto = True
                # sólo operable si buy()/get_candles() reconocen el nombre
                if cand in conocidos:
                    elegido = cand
                elif par in conocidos:
                    elegido = par
                break
        if elegido:
            abiertos.append(elegido)
            log(f"[PARES-NORMALES] {par} — ABIERTO (operará como '{elegido}').")
        elif estaba_abierto:
            log(f"[PARES-NORMALES] {par} — abierto, pero buy()/get_candles() no reconocen el nombre; se omite.")
        elif por_nombre.get(par) or por_nombre.get(par + "-op"):
            log(f"[PARES-NORMALES] {par} — cerrado ahora.")
        else:
            log(f"[PARES-NORMALES] {par} — no aparece con ese nombre (revisa /diagnostico).")

    if abiertos:
        log(f"[PARES-NORMALES] Abiertos ahora: {', '.join(abiertos)}")
    else:
        log("[PARES-NORMALES] Ninguno de los pares normales está abierto ahora.")
    return abiertos, False


def _con_timeout(func, timeout_s, *args, **kwargs):
    """
    Ejecuta 'func' en un hilo separado y espera como máximo 'timeout_s'
    segundos. Necesario porque algunas llamadas de la librería de
    IQ Option (sobre todo .connect()) NO tienen timeout propio: con
    internet intermitente (no totalmente caído, sino "a medias") pueden
    quedarse colgadas esperando respuesta del servidor durante minutos
    u horas, sin lanzar ninguna excepción — congelando todo el bucle de
    reconexión en silencio.

    Devuelve (completo: bool, resultado, excepcion). Si completo=False,
    la llamada sigue corriendo en segundo plano (el hilo es daemon, así
    que no bloquea el cierre del programa) pero ya no se espera más —
    el bucle que llamó a esto sigue con su próximo intento.
    """
    resultado = {}

    def _target():
        try:
            resultado["valor"] = func(*args, **kwargs)
        except Exception as e:
            resultado["excepcion"] = e

    hilo = threading.Thread(target=_target, daemon=True)
    hilo.start()
    hilo.join(timeout=timeout_s)

    if hilo.is_alive():
        return False, None, None   # se quedó colgada, no esperamos más
    if "excepcion" in resultado:
        return True, None, resultado["excepcion"]
    return True, resultado.get("valor"), None


def _forzar_reconexion(motivo=""):
    """Fuerza una reconexión limpia del socket cuando se detecta que la
    conexión quedó 'colgada' (p.ej. get_candles() sin respuesta pese a que
    check_connect() dice que sigue conectado). Protegida contra tormentas:
    si ya se reconectó hace menos de 15s, no lo repite, para que los 4
    hilos no reconecten a la vez."""
    global _ultima_reconexion_forzada
    ahora = time.time()
    if ahora - _ultima_reconexion_forzada < 15:
        return
    _ultima_reconexion_forzada = ahora
    with _lock:
        estado["conectado"] = False
    log(f"[BOT] Forzando reconexión limpia{(' — ' + motivo) if motivo else ''}...")
    try:
        with _conn_lock:
            completo, res, exc = _con_timeout(_Iq.connect, 20)
        if completo and exc is None and res and res[0]:
            try:
                _Iq.change_balance(config["modo"])
            except Exception:
                pass
            with _lock:
                estado["conectado"] = True
                estado["error"]     = ""
            log("[BOT] Reconexión forzada OK.")
        else:
            log("[BOT] Reconexión forzada no confirmada; se seguirá intentando.")
    except Exception as e:
        log(f"[BOT] Error en reconexión forzada: {e}")


def loop_bot(mi_run_id):
    global _Iq

    _nombre_broker = "Deriv" if broker_actual(config) == "deriv" else "IQ Option"
    log(f"Conectando con {_nombre_broker}...")
    try:
        _Iq = crear_conector(config, logger=log)
        completo, conn_result, exc = _con_timeout(_Iq.connect, 25)
        if not completo:
            log("Conexión inicial colgada (sin respuesta tras 25s). Revisa tu internet e inicia de nuevo.")
            with _lock:
                estado["corriendo"] = False
                estado["error"]     = "Conexión inicial sin respuesta (timeout). Reinicia el bot."
            return
        if exc is not None:
            raise exc
        ok, reason = conn_result
        if not ok:
            log(f"Error de conexión: {reason}")
            with _lock:
                estado["corriendo"] = False
                estado["error"]     = str(reason)
            return
        _Iq.change_balance(config["modo"])
        # RELIABILITY: get_balance inicial con timeout. Antes colgaba
        # silenciosamente si IQ Option no respondía.
        completo_b, bal, exc_b = _con_timeout(_Iq.get_balance, 15)
        if not completo_b or exc_b is not None or bal is None:
            log("[WARN] No pude leer el balance inicial tras conectar. El bot sigue; el balance se actualizará en la próxima operación.")
            bal = 0.0
        with _lock:
            estado["conectado"] = True
            estado["balance"]   = round(bal, 2)
        log(f"Conectado | {config['modo']} | Balance: ${bal:.2f}")
        enviar_telegram(
            f"🤖 <b>BOT JPH TRADING conectado</b>\n"
            f"Modo: {config['modo']}\n"
            f"Balance: <b>${bal:.2f}</b>\n"
            f"Pares: {', '.join(config['pares'])}"
        )
    except Exception as e:
        log(f"Error al conectar: {e}")
        with _lock:
            estado["corriendo"] = False
            estado["error"]     = str(e)
        return

    # ── Limpiar apagados de la corrida anterior (rotación) ──────────
    _pares_desactivados.clear()
    _modo_por_par.clear()
    _lista_negra.clear()
    with _entrada_lock:
        _entradas_por_minuto.clear()

    # ── Registrar TODOS los activos del servidor en la tabla de la
    # librería, para que get_candles()/buy() reconozcan CUALQUIER par
    # (no solo la lista corta precargada). Esto arregla el "solo
    # funcionan los primeros pares". ────────────────────────────────
    registrar_activos_operables(list(config.get("pares", [])) +
                                list(config.get("pares_normales", [])))

    # ── Construir lista efectiva de pares a operar ──────────────────
    # Siempre incluye los pares OTC configurados. Si está activada la
    # opción de mercado normal, se agregan SOLO los pares normales que
    # la API reporte como abiertos en este momento (nunca se fuerza un
    # par cerrado).
    # Bróker DERIV: se operan los índices sintéticos configurados. El
    # escáner y la auto-calibración son de forex y NO aplican a sintéticos,
    # así que se saltan (se opera la lista tal cual, como en modo manual).
    _es_deriv = broker_actual(config) == "deriv"
    if _es_deriv:
        pares_a_operar = list(config.get("pares_deriv") or [])
        log(f"[MODO] Bróker DERIV — operando sintéticos ({len(pares_a_operar)}): "
            f"{', '.join(pares_a_operar) or '(lista vacía: configura pares_deriv)'}. "
            f"Escáner/auto-calibración de forex desactivados para este bróker.")
    else:
        pares_a_operar = list(config["pares"])
    abiertos = []
    if config.get("pares_normales_activo", False) and not _es_deriv:
        deseados = config.get("pares_normales", [])
        abiertos, _hubo_fallo_inicial = obtener_pares_normales_abiertos(deseados)
        if abiertos:
            log(f"[PARES-NORMALES] Abiertos ahora: {', '.join(abiertos)}")
            enviar_telegram(
                "🌍 <b>Mercado normal — pares abiertos ahora</b>\n" +
                "\n".join(f"✅ {p}" for p in abiertos)
            )
        else:
            log("[PARES-NORMALES] Ninguno de los pares normales configurados está abierto ahora.")
        pares_a_operar += abiertos

    # ── MODO DE OPERACIÓN: en "manual" el usuario decide EXACTAMENTE qué
    # pares operar y con qué modo (via UI: detectar pares + backtest 1 día
    # + toggles). No corremos autocal ni scanner automáticos al arrancar
    # — la selección la controla el usuario, no el bot. ───────────────
    modo_op = config.get("modo_operacion", "manual")
    if modo_op == "manual":
        log(f"[MODO] Operación MANUAL — se opera lo que el usuario tiene "
            f"marcado ({len(pares_a_operar)} pares). Cambios se aplican en caliente.")

    # ── AUTO-CALIBRACIÓN: elegir pares por BORDE MEDIDO (backtest real
    # de cada candidato), cada par con SU modo ganador. Si funciona, el
    # escáner de tendencia no se usa (queda de respaldo). Solo aplica a
    # la estrategia IFC, que es la que tiene modos calibrables. ─────────
    autocal_ok = False
    if (modo_op != "manual"
            and not _es_deriv
            and config.get("autocalibracion_activa", False)
            and config.get("estrategia", "ifc") == "ifc"):
        candidatos_cal = list(dict.fromkeys(
            list(config.get("scanner_candidatos_otc", [])) +
            list(config.get("pares", [])) +
            abiertos
        ))
        seleccion, _tabla_cal = calibrar_pares(candidatos_cal)
        if seleccion:
            _modo_por_par.clear()
            for p, m, w in seleccion:
                _modo_por_par[p] = m
            pares_a_operar = [p for p, m, w in seleccion]
            autocal_ok = True
            log("[AUTOCAL] ✅ Operando por borde medido: " +
                ", ".join(f"{p} ({m} {w}%)" for p, m, w in seleccion))
            enviar_telegram(
                "🎯 <b>Auto-calibración — pares con borde medido</b>\n" +
                "\n".join(f"⭐ {p} · {m} · {w}%" for p, m, w in seleccion))
        elif estado.get("corriendo", False):
            log(f"[AUTOCAL] Ningún par superó el umbral de "
                f"{config.get('autocal_umbral', 54.0)}%. Se usa el escáner "
                f"de tendencia como respaldo.")

    # ── ESCÁNER integrado: elegir los mejores pares antes de operar ──
    # Escanea todos los candidatos (lista amplia de OTC + los pares
    # normales que estén abiertos) y se queda SOLO con los scanner_top_n
    # de mejor tendencia. Después, los hilos de señales IFC trabajan
    # únicamente con esos elegidos.
    if modo_op != "manual" and not _es_deriv and not autocal_ok and config.get("scanner_activo", False):
        candidatos = list(dict.fromkeys(
            list(config.get("scanner_candidatos_otc", [])) +
            list(config.get("pares", [])) +
            abiertos
        ))
        top_n = max(1, int(config.get("scanner_top_n", 4)))
        elegidos, _ranking = escanear_y_elegir_pares(candidatos, top_n)
        if elegidos:
            pares_a_operar = elegidos
            enviar_telegram(
                "🔎 <b>Escáner — pares elegidos para operar</b>\n" +
                "\n".join(f"⭐ {r['par']} · score {r['score']} · {r['direccion']}"
                          for r in _ranking[:top_n])
            )
        elif estado.get("corriendo", False):
            log("[SCAN] El escaneo no dio resultados; se usan los pares configurados a mano.")

    with _lock:
        estado["pares_operando"] = pares_a_operar   # para que la UI/Telegram sepan la lista real

    hilos = []
    for idx, par in enumerate(pares_a_operar):
        t = threading.Thread(target=loop_par, args=(par, idx, mi_run_id), daemon=True, name=f"hilo_{par}")
        t.start()
        hilos.append(t)
        log(f"[BOT] Hilo lanzado para {par} (slot {idx})")

    # WATCHDOG: lanza el hilo que vigila los heartbeats de cada loop_par.
    # Si un par deja de avanzar, fuerza una reconexión limpia del socket.
    threading.Thread(target=_watchdog_loop, args=(mi_run_id,), daemon=True,
                     name="watchdog").start()
    log("[BOT] Watchdog iniciado (vigilancia de hilos por par).")

    # Reconexión: reintentos acotados con backoff creciente. Si tras
    # varios intentos seguidos no logra reconectar, se rinde y detiene
    # el bot por completo en vez de quedarse "Reconectando..." para
    # siempre — así el usuario ve el error real y puede reiniciar limpio.
    intentos_reconexion = 0
    MAX_INTENTOS_RECONEXION = 8   # ~ suficiente para cortes breves de internet

    # ── Re-chequeo periódico de pares normales ───────────────────────
    # La consulta de "qué pares normales están abiertos" se hacía SOLO
    # una vez, al conectar. Si el mercado normal abría DESPUÉS de que el
    # bot ya estaba corriendo, nunca se enteraba — se quedaba operando
    # solo los OTC para siempre en esa corrida. Ahora se vuelve a
    # consultar cada PARES_NORMALES_RECHEQUEO_S segundos, y cualquier
    # par nuevo que aparezca abierto se lanza en caliente, sin reiniciar
    # el bot.
    PARES_NORMALES_RECHEQUEO_S = 300   # cada 5 minutos (caso normal, conexión estable)
    PARES_NORMALES_RECHEQUEO_MAX_S = 1800   # tope de 30 min si la conexión está inestable
    ultimo_rechequeo_normales  = time.time()
    fallos_consecutivos_normales = 0
    pares_normales_operando    = set(p for p in pares_a_operar if p in config.get("pares_normales", []))
    pares_normales_abiertos_actual = set(abiertos)   # normales abiertos (candidatos de rotación)
    ultimo_rescan = time.time()                       # temporizador de la rotación automática

    while estado["corriendo"] and _run_id == mi_run_id:
        try:
            conectado_ahora = False
            try:
                completo_chk, chk_result, exc_chk = _con_timeout(_Iq.check_connect, 10)
                if not completo_chk:
                    log("[BOT] check_connect() sin respuesta tras 10s, se asume desconectado.")
                    conectado_ahora = False
                elif exc_chk is not None:
                    log(f"[BOT] check_connect() falló: {exc_chk}")
                    conectado_ahora = False
                else:
                    conectado_ahora = bool(chk_result)
            except Exception as e:
                log(f"[BOT] check_connect() falló: {e}")
                conectado_ahora = False

            if not conectado_ahora:
                with _lock:
                    estado["conectado"] = False
                intentos_reconexion += 1
                espera = min(5 * intentos_reconexion, 30)   # backoff: 5,10,...30s tope
                log(f"Reconectando... (intento {intentos_reconexion}/{MAX_INTENTOS_RECONEXION})")
                try:
                    # Límite duro de 20s: si .connect() se cuelga (típico
                    # con internet "a medias", no totalmente caído), no
                    # nos quedamos esperando — se cuenta como intento
                    # fallido y se sigue con el siguiente, en vez de
                    # congelar el bucle entero por minutos u horas.
                    global _ultima_reconexion_forzada
                    _ultima_reconexion_forzada = time.time()  # evita que un hilo fuerce otra a la vez
                    with _conn_lock:
                        completo, conn_result, exc = _con_timeout(_Iq.connect, 20)
                    if not completo:
                        log("Reintento de conexión sin respuesta tras 20s, se descarta y se reintenta.")
                    elif exc is not None:
                        log(f"[BOT] Excepción al reconectar: {exc}")
                    else:
                        ok, reason = conn_result
                        if ok:
                            _Iq.change_balance(config["modo"])
                            with _lock:
                                estado["conectado"] = True
                                estado["error"]     = ""
                            log("Reconectado correctamente.")
                            intentos_reconexion = 0
                        else:
                            log(f"Reintento de conexión fallido: {reason}")
                except Exception as e:
                    log(f"[BOT] Excepción al reconectar: {e}")

                if intentos_reconexion >= MAX_INTENTOS_RECONEXION:
                    log("No se pudo reconectar tras varios intentos. Deteniendo el bot.")
                    with _lock:
                        estado["corriendo"] = False
                        estado["error"]     = "Se perdió la conexión y no se pudo reconectar. Reinicia el bot."
                    enviar_telegram(
                        "⚠️ <b>BOT JPH TRADING detenido</b>\n"
                        "No se pudo reconectar a IQ Option tras varios intentos.\n"
                        "Revisa tu conexión a internet y vuelve a iniciar el bot."
                    )
                    break

                time.sleep(espera)
                continue
            else:
                with _lock:
                    estado["conectado"] = True
                intentos_reconexion = 0

                # Re-chequeo de pares normales: solo si está activado (y
                # solo en modo auto — en manual el usuario añade los
                # pares normales que quiera desde la UI). Cada
                # PARES_NORMALES_RECHEQUEO_S segundos con backoff si la
                # conexión falla.
                if modo_op != "manual" and config.get("pares_normales_activo", False):
                    ahora_ts = time.time()
                    intervalo_actual = min(
                        PARES_NORMALES_RECHEQUEO_S * (2 ** fallos_consecutivos_normales),
                        PARES_NORMALES_RECHEQUEO_MAX_S,
                    )
                    if ahora_ts - ultimo_rechequeo_normales >= intervalo_actual:
                        ultimo_rechequeo_normales = ahora_ts
                        deseados = config.get("pares_normales", [])
                        abiertos_lista, hubo_fallo = obtener_pares_normales_abiertos(deseados)

                        if hubo_fallo:
                            fallos_consecutivos_normales += 1
                            proximo_intervalo_min = min(
                                PARES_NORMALES_RECHEQUEO_S * (2 ** fallos_consecutivos_normales),
                                PARES_NORMALES_RECHEQUEO_MAX_S,
                            ) / 60
                            log(f"[PARES-NORMALES] Fallo #{fallos_consecutivos_normales} al consultar "
                                f"(conexión inestable). Próximo intento en ~{proximo_intervalo_min:.0f} min.")
                        else:
                            fallos_consecutivos_normales = 0   # se restablece en cuanto vuelve a funcionar

                        abiertos_ahora = set(abiertos_lista)
                        if not hubo_fallo:
                            pares_normales_abiertos_actual = abiertos_ahora
                        nuevos = abiertos_ahora - pares_normales_operando

                        if nuevos and config.get("scanner_activo", False):
                            # Con el escáner activo, los pares que abren NO
                            # se lanzan directo: entran a competir en el
                            # próximo re-escaneo de la rotación, respetando
                            # el "solo los N mejores".
                            pares_normales_operando |= nuevos
                            log("[PARES-NORMALES] Abrieron: " + ", ".join(sorted(nuevos)) +
                                " — competirán en el próximo re-escaneo del escáner.")
                        elif nuevos:
                            for par in sorted(nuevos):
                                idx_nuevo = len(pares_a_operar)
                                pares_a_operar.append(par)
                                pares_normales_operando.add(par)
                                t = threading.Thread(
                                    target=loop_par, args=(par, idx_nuevo, mi_run_id),
                                    daemon=True, name=f"hilo_{par}"
                                )
                                t.start()
                                hilos.append(t)
                                log(f"[PARES-NORMALES] {par} abrió — hilo lanzado en caliente (slot {idx_nuevo}).")
                            with _lock:
                                estado["pares_operando"] = list(pares_a_operar)
                            enviar_telegram(
                                "🌍 <b>Mercado normal — nuevos pares abiertos</b>\n" +
                                "\n".join(f"✅ {p}" for p in sorted(nuevos)) +
                                "\nSe agregaron a la operativa sin reiniciar el bot."
                            )

                        # Pares que ya no están operando y siguieron abiertos no se
                        # quitan aquí — si cierran, sus propios hilos lo manejarán
                        # al fallar get_candles (igual que cualquier otro cierre de
                        # mercado), evitando parar hilos a mitad de una operación.

            # ── REFRESCO DE PAYOUTS (para el filtro facundo_payout_min) ─
            # Se hace aquí, fuera de la ventana :57→:00 de los hilos de
            # par, para que loop_par solo LEA el cache y nunca espere a
            # get_all_profit() en la ruta caliente.
            if (conectado_ahora
                    and config.get("estrategia") == "facundo"
                    and float(config.get("facundo_payout_min", 0) or 0) > 0):
                _refrescar_payout_cache()

            # ── SINCRONIZACIÓN MANUAL EN CALIENTE ───────────────────────
            # En modo "manual", la lista real de pares operando debe seguir
            # a config['pares'] (los que el usuario tiene marcados en la
            # UI). Cuando el usuario marca uno nuevo, se lanza su hilo sin
            # reiniciar el bot. Cuando lo desmarca, ese par se agrega a
            # _pares_desactivados y su hilo termina la operación en curso
            # (si la hay) y se apaga solo. Los cambios de MODO (C/R) por
            # par no requieren nada aquí: _analizar_ifc lee config en cada
            # decisión, así que aplican automáticamente.
            if conectado_ahora and modo_op == "manual":
                try:
                    pares_deseados = list(dict.fromkeys(config.get("pares") or []))
                    operando_efectivo = set(p for p in pares_a_operar
                                            if p not in _pares_desactivados)

                    # Pares nuevos (marcados por el usuario y aún sin hilo activo)
                    for par_nuevo in pares_deseados:
                        if par_nuevo in operando_efectivo:
                            continue
                        _pares_desactivados.discard(par_nuevo)  # por si estaba desactivado antes
                        if par_nuevo in pares_a_operar:
                            # Estaba en la lista pero desactivado: al quitar del
                            # set _pares_desactivados NO revive el hilo (el hilo
                            # ya se apagó). Lanzamos uno nuevo con su mismo slot.
                            try:
                                idx_nuevo = pares_a_operar.index(par_nuevo)
                            except ValueError:
                                idx_nuevo = len(pares_a_operar)
                        else:
                            idx_nuevo = len(pares_a_operar)
                            pares_a_operar.append(par_nuevo)
                        t = threading.Thread(target=loop_par,
                                             args=(par_nuevo, idx_nuevo, mi_run_id),
                                             daemon=True, name=f"hilo_{par_nuevo}")
                        t.start()
                        hilos.append(t)
                        log(f"[MANUAL] Par añadido en caliente: {par_nuevo} "
                            f"(modo {_obtener_modo_par(par_nuevo)})")

                    # Pares que el usuario desmarcó: apagar su hilo (termina la
                    # operación en curso y se detiene solo). No se sacan de
                    # pares_a_operar, solo se marcan como desactivados.
                    for par_op in list(operando_efectivo):
                        if par_op not in pares_deseados:
                            _pares_desactivados.add(par_op)
                            log(f"[MANUAL] Par desmarcado por el usuario: {par_op} "
                                f"— su hilo termina la operación en curso y se apaga.")

                    with _lock:
                        estado["pares_operando"] = [p for p in pares_a_operar
                                                    if p not in _pares_desactivados]
                except Exception as e:
                    log(f"[MANUAL] Error sincronizando pares: {e}")

            # ── LISTA NEGRA en vivo: banquear pares con mal desempeño ──
            if conectado_ahora and config.get("lista_negra_activa", False):
                try:
                    _baneados = _revisar_lista_negra()
                    if _baneados:
                        with _lock:
                            estado["pares_operando"] = list(dict.fromkeys(
                                p for p in pares_a_operar if p not in _pares_desactivados))
                except Exception as e:
                    log(f"[LISTA-NEGRA] Error: {e}")

            # ── SUSTITUCIÓN POR RACHA: par con N pérdidas seguidas sale,
            # entra en caliente el siguiente mejor de la calibración. ────
            if conectado_ahora and config.get("racha_sustitucion_activa", False):
                try:
                    max_racha = max(1, int(config.get("par_perdidas_max", 3)))
                    operando_ahora = [p for p in dict.fromkeys(pares_a_operar)
                                      if p not in _pares_desactivados]
                    for par_mal in operando_ahora:
                        if _racha_perdidas_par(par_mal) < max_racha:
                            continue
                        _pares_desactivados.add(par_mal)
                        log(f"[RACHA] {par_mal}: {max_racha} pérdidas seguidas — se apaga "
                            f"(termina su operación en curso) y no vuelve en esta sesión.")
                        sustituto = _elegir_sustituto(pares_a_operar)
                        if sustituto:
                            s_par, s_modo, s_val = sustituto
                            _pares_desactivados.discard(s_par)
                            if s_modo:
                                _modo_por_par[s_par] = s_modo
                            try:
                                idx_nuevo = pares_a_operar.index(par_mal)  # reutiliza su slot
                            except ValueError:
                                idx_nuevo = len(pares_a_operar)
                            pares_a_operar.append(s_par)
                            t = threading.Thread(target=loop_par,
                                                 args=(s_par, idx_nuevo, mi_run_id),
                                                 daemon=True, name=f"hilo_{s_par}")
                            t.start()
                            hilos.append(t)
                            etiqueta = f" ({s_modo} {s_val}%)" if s_modo else (f" (score {s_val})" if s_val is not None else "")
                            log(f"[RACHA] ENTRA {s_par}{etiqueta} en el lugar de {par_mal}.")
                            enviar_telegram("🔁 <b>Sustitución por racha</b>\n"
                                            f"➖ {par_mal}: {max_racha} pérdidas seguidas\n"
                                            f"➕ {s_par}{etiqueta}")
                        else:
                            log(f"[RACHA] Sin sustituto disponible; se sigue con los pares restantes.")
                            enviar_telegram(f"🔁 <b>Sustitución por racha</b>\n➖ {par_mal} fuera "
                                            f"({max_racha} seguidas). Sin sustituto disponible.")
                        with _lock:
                            estado["pares_operando"] = list(dict.fromkeys(
                                p for p in pares_a_operar if p not in _pares_desactivados))
                except Exception as e:
                    log(f"[RACHA] Error: {e}")

            # ── ROTACIÓN AUTOMÁTICA (re-escaneo periódico) ────────────
            # Cada scanner_rotacion_min minutos re-escanea los candidatos;
            # si un par operando quedó LATERAL, lo apaga (su hilo termina
            # la operación en curso y se detiene solo) y lanza en caliente
            # el mejor candidato con tendencia clara.
            # NOTA: con la auto-calibración activa (pares por borde medido)
            # o el modo manual, esta rotación se desactiva — el usuario o
            # la calibración ya decidieron qué se opera, y cambiar pares
            # "en tendencia" desharía esa elección.
            if (conectado_ahora
                    and modo_op != "manual"
                    and not autocal_ok
                    and config.get("scanner_activo", False)
                    and config.get("scanner_rotacion_activa", False)
                    and time.time() - ultimo_rescan >= max(5, int(config.get("scanner_rotacion_min", 30))) * 60):
                ultimo_rescan = time.time()
                try:
                    candidatos_rot = list(dict.fromkeys(
                        list(config.get("scanner_candidatos_otc", [])) +
                        list(config.get("pares", [])) +
                        sorted(pares_normales_abiertos_actual)
                    ))
                    candidatos_rot = [p for p in candidatos_rot if p not in _lista_negra]
                    log(f"[ROTACION] Re-escaneo periódico de {len(candidatos_rot)} candidatos...")
                    _eleg_rot, ranking_rot = escanear_y_elegir_pares(
                        candidatos_rot, int(config.get("scanner_top_n", 4)))
                    operando_ahora = list(dict.fromkeys(
                        p for p in pares_a_operar if p not in _pares_desactivados))
                    cambios = _decidir_rotacion(
                        ranking_rot, operando_ahora,
                        int(config.get("scanner_rotacion_score_min", 55)))
                    if not cambios:
                        log("[ROTACION] Los pares operando mantienen buena tendencia. Sin cambios.")
                    for sale, entra in cambios:
                        _pares_desactivados.add(sale["par"])
                        _pares_desactivados.discard(entra["par"])
                        try:
                            idx_nuevo = pares_a_operar.index(sale["par"])   # reutiliza su slot
                        except ValueError:
                            idx_nuevo = len(pares_a_operar)
                        pares_a_operar.append(entra["par"])
                        if entra["par"] in config.get("pares_normales", []):
                            pares_normales_operando.add(entra["par"])
                        t = threading.Thread(
                            target=loop_par, args=(entra["par"], idx_nuevo, mi_run_id),
                            daemon=True, name=f"hilo_{entra['par']}")
                        t.start()
                        hilos.append(t)
                        log(f"[ROTACION] SALE {sale['par']} (LATERAL, score {sale['score']}) → "
                            f"ENTRA {entra['par']} (score {entra['score']}, {entra['direccion']}). "
                            f"El saliente termina su operación en curso y se apaga.")
                        enviar_telegram(
                            "🔄 <b>Rotación de pares</b>\n"
                            f"➖ Sale: {sale['par']} (lateral, score {sale['score']})\n"
                            f"➕ Entra: {entra['par']} (score {entra['score']}, {entra['direccion']})")
                    if cambios:
                        with _lock:
                            estado["pares_operando"] = list(dict.fromkeys(
                                p for p in pares_a_operar if p not in _pares_desactivados))
                except Exception as e:
                    log(f"[ROTACION] Error en el re-escaneo: {e}")

            time.sleep(5)
        except KeyboardInterrupt:
            log("[BOT] Detenido manualmente.")
            break
        except Exception:
            log("[ERROR] " + traceback.format_exc())
            time.sleep(5)

    with _lock:
        if _run_id == mi_run_id:
            estado["conectado"] = False
    log("Bot detenido.")

# =============================================================
# CONTROL EXTERNO (usado por servidor.py / app.py)
# =============================================================

def iniciar_bot():
    global _hilo_bot, _run_id
    cargar_config()
    with _lock:
        if estado["corriendo"]:
            return
        # Nueva generación: cualquier hilo de una corrida anterior que
        # haya quedado vivo (zombi, bloqueado en una llamada de red)
        # se identificará como obsoleto y se cerrará solo.
        _run_id += 1
        mi_run_id = _run_id
        # Reset SOLO de flags de ejecución. Los contadores del día
        # (wins, losses, neto, racha, operaciones, bloqueos) los tocamos
        # después según si hay o no una sesión persistente de HOY.
        estado.update({
            "corriendo":              True,
            "conectado":              False,
            "error":                  "",
            "warmup_resultados":      {},
            "warmup_ranking_enviado": False,
        })

    # ── Sesión persistente: si hay un guardado de HOY, lo cargamos. Si
    # es de otro día (o no hay), se empieza de cero — así detener y
    # volver a iniciar el bot NO reinicia la cuenta del día. ─────────
    cargado = False
    if config.get("sesion_persistente", True):
        cargado = cargar_sesion()
    if cargado:
        with _lock:
            w, l, n = estado["wins"], estado["losses"], estado["neto"]
            ops = len(estado["operaciones"])
        log(f"[SESION] Continuando sesión de hoy: {w}W / {l}L / neto ${n:.2f} · {ops} operaciones registradas.")
    else:
        # Sin sesión persistente de hoy → nuevo día (o feature desactivada).
        with _lock:
            estado.update({
                "wins":              0,
                "losses":            0,
                "neto":              0.0,
                "perdidas_seguidas": 0,
                "operaciones":       [],
                "pares_bloqueados":  {},
            })
        # Borrar cualquier sesion.json de un día viejo, si existía.
        try:
            if os.path.exists(SESION_FILE):
                os.remove(SESION_FILE)
        except Exception:
            pass

    # Si el hilo de la corrida anterior sigue vivo (no llegó a salir
    # solo todavía), le damos un margen corto para terminar. No bloqueamos
    # el arranque del nuevo por esto: si no terminó a tiempo, simplemente
    # queda marcado como de otra generación y se autodescartará en su
    # próxima vuelta del bucle.
    if _hilo_bot is not None and _hilo_bot.is_alive():
        _hilo_bot.join(timeout=2)

    _hilo_bot = threading.Thread(target=loop_bot, args=(mi_run_id,), daemon=True)
    _hilo_bot.start()

def detener_bot():
    with _lock:
        estado["corriendo"] = False
    log("Señal de parada enviada.")

def get_estado():
    with _lock:
        snapshot = dict(estado)
    snapshot["desempeno_por_par"]      = calcular_desempeno_por_par(snapshot["operaciones"])
    snapshot["desempeno_por_horario"]  = calcular_desempeno_por_horario(snapshot["operaciones"])
    return snapshot

def set_config(nueva):
    # SECURITY: si 'nueva' trae password vacío (porque la UI no lo
    # reescribió al cargar config), NO sobrescribimos el password guardado.
    # Así un "Guardar configuración" sin re-escribir la contraseña no la
    # borra del config.json.
    if nueva is not None and "password" in nueva and not nueva.get("password"):
        nueva = dict(nueva)
        nueva.pop("password", None)
    # Mismo blindaje para el token de Deriv: si viene vacío, no lo borramos.
    if nueva is not None and "deriv_token" in nueva and not nueva.get("deriv_token"):
        nueva = dict(nueva)
        nueva.pop("deriv_token", None)
    config.update(nueva)
    guardar_config()

iniciar = iniciar_bot
detener = detener_bot

# Arranca la escucha de /estado de Telegram en segundo plano.
# (Comandos /encender y /apagar eliminados)
_hilo_telegram_comandos = threading.Thread(target=_telegram_comandos_loop, daemon=True)
_hilo_telegram_comandos.start()
