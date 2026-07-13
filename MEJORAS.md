# Bot IFC Auto — Versión Mejorada

## ⭐ RONDA v9: VUELTA A LA CONFIGURACIÓN VALIDADA

Tras la sesión del 11/7 con la regla estricta (2–3 previos, sep 5): 56.9% en 51 ops, P(resultado | sin borde) = 40% — no mostró mejora sobre la versión anterior (62.5% en 56 ops, P = 9%). Además la muestra vino contaminada por pares nuevos sin historial (TIAUSD, JUPUSD, ALIBABA, US100/JP225); los 3 pares habituales quedaron en 52.3%, bajo break-even.

**Defaults de esta versión** (decisión del usuario, para comparación limpia de un día):
- `facundo_sr_toques_max_previos: 0` (sin tope)
- `facundo_sr_separacion_velas: 3`

Verificado señal por señal: con estos valores las entradas son **IDÉNTICAS (240/240)** a la versión que dio 62.5%. Nota: se volvieron AMBOS parámetros porque con tope 0 pero separación 5 las señales aún diferían (220 vs 240) — un cambio a la vez exige replicar la config completa.

El mínimo del 3er toque (2 previos de velas distintas y separadas) sigue activo, como en todas las versiones.

---

## ⭐ RONDA: REGLA DE TOQUES DEL USUARIO (v8) — 2 a 3 previos, separación 5

Aclaración importante: el MÍNIMO del 3er toque nunca se quitó en ninguna versión — un nivel virgen o con 1 solo toque previo jamás genera señal. Lo que estuvo cambiando fue el TECHO.

### Defaults nuevos (decisión del usuario)
- `facundo_sr_toques_max_previos: 3` — la entrada solo ocurre en el **3er o 4to toque** del nivel; un nivel más golpeado se considera agotado (evita entrar en tendencias/zonas ya gastadas). Poner `2` = solo 3er toque exacto; `0` = sin techo (comportamiento de la versión validada con 62.5%).
- `facundo_sr_separacion_velas: 5` — un toque nuevo solo cuenta si llega **al menos 5 velas después** del anterior (semántica exacta: distancia de índices ≥ 5, o sea 4+ velas intermedias). Congestión de velas seguidas en la zona = UN solo toque.

### Impacto medido (misma serie de 3000 velas, config invertir+EMA activa)
- Versión validada en vivo: 240 señales · Regla nueva: **111 señales** (~2.2/hora/par, antes 4.8)
- 96 en común, 15 nuevas, 144 de la validada que ya no entran
- Conclusión estadística: es una **población de señales distinta** de la que dio 62.5% en 56 ops. La hipótesis "niveles muy golpeados = agotados" es razonable, pero está SIN validar. Antes de la próxima sesión: correr "Backtest 1 día · seleccionados" (o el de 8 filas) y comparar el winrate de esta regla contra el break-even real del payout.

---

## ⭐ RONDA: AUDITORÍA corregido↔v6 + TELEGRAM ROBUSTO (v7)

### 1. Comparación de la "manera de entrar" entre versiones — resultado
Auditadas las 8 funciones de decisión de Facundo entre `bot_facundo_corregido` (la versión de la sesión validada 62.5% en 56 ops) y v6, función por función y señal por señal sobre 3000 velas idénticas. **Única diferencia real: el tope `facundo_sr_toques_max_previos`** (añadido en v5). Todo lo demás (niveles, separación, 3er toque, filtro EMA, inversión, timing de entrada) es idéntico.

**Hallazgo importante**: el tope=3 no solo descartaba señales de niveles gastados — al filtrar un nivel del conjunto, el detector encontraba OTRO nivel en la zona y emitía señales que antes no existían. Medido: 240 señales → 99, y NO como subconjunto. Es decir, una población de señales distinta y sin validar en vivo.

**Decisión**: el default vuelve a `0` (sin tope) → entradas verificadas IDÉNTICAS señal por señal a la versión validada. El tope queda disponible como opción; si quieres probar la regla estricta 2–3 toques, valídala primero con el backtest de 8 filas.

### 2. Telegram: fallback de proxy + diagnóstico en 2 pasos
Causa raíz probable de "mismos datos y da error": el código forzaba `trust_env=False` (ignorar el proxy del sistema) en TODAS las llamadas a Telegram. En redes con VPN, proxy corporativo o antivirus que intercepta HTTPS, eso mata la conexión siempre. Ahora `_tg_request()` intenta ambos modos (directo ↔ proxy del sistema) y **recuerda el que funcionó** para las siguientes llamadas. Aplica a envíos, al botón Probar y al loop de comandos /estado.

El botón **📨 Probar Telegram** ahora diagnostica en 2 pasos: PASO 1 `getMe` (¿hay conexión? ¿el token es válido?) y PASO 2 `sendMessage` (¿el chat existe?). El resultado dice exactamente dónde falla, con la clase de excepción de red incluida (SSLError = antivirus/proxy interceptando; ConnectionError = sin salida a api.telegram.org) y el @nombre del bot cuando el token valida.

### 3. CSRF viejo tras reiniciar el servidor
Si reiniciabas `iniciar.bat` con la página abierta, el token CSRF del navegador quedaba obsoleto y TODOS los botones POST devolvían 403 ("da error" sin explicación). Ahora `postJSON` renueva el token y reintenta automáticamente una vez.

---

## ⭐ RONDA: RANGO DE TOQUES 2–3 + ETIQUETA DE PRIORIDAD CLARA

### 1. Falsa alarma aclarada: "toques 0" en el log de PRIORIDAD
El usuario reportó una entrada con "candidato con prioridad 90 (conf 79 + toques 0 + adx 11)" creyendo que violó la regla del 3er toque. **No la violó**: la línea de la señal decía "toque #3". Ese "toques 0" era el BONO de puntos por toques extra sobre el mínimo (3−3=0), no el conteo del nivel. La etiqueta ahora es inequívoca: `conf 79 + bono +0 por toque #3 + adx 14`.

### 2. Tope máximo de toques previos (regla completa del usuario: 2 a 3 previos)
Nueva config `facundo_sr_toques_max_previos: 3` — solo se entra en el **3er o 4to toque** del nivel. Un nivel más golpeado (5+, 6+ toques) se considera gastado y se descarta. Aplica a rechazo, rompimiento y lateral. Poner `0` = sin tope (comportamiento anterior). Verificado: 1 previo descartado, 2–3 previos dan señal, 4+ descartados.

---

## ⭐ RONDA: BACKTEST SIN ARRANCAR EL BOT + SELECCIONAR TODO

### 1. Backtest y detección de pares SIN iniciar el bot
"Detectar pares disponibles", "Backtest 1 día · seleccionados" y la página /backtest ya **no requieren el bot corriendo**: si está detenido, se abre automáticamente una conexión de **solo-datos** con las credenciales guardadas (solo lee velas/instrumentos/payouts, no opera). Requisito único: haber guardado usuario y contraseña en Configuración. Cuando inicias el bot después, su conexión normal reemplaza a la de datos sin conflicto.

### 2. Botón "Seleccionar todo"
En la sección de pares, entre Detectar y Backtest. Marca todos los pares listados (OTC + normales detectados) de un clic; si ya están todos marcados, se convierte en "Quitar todos". Flujo completo nuevo: abrir la interfaz → Detectar pares → Seleccionar todo → Backtest 1 día → revisar la tabla → marcar/configurar → Iniciar bot.

### 3. El backtest masivo corre LA ESTRATEGIA ACTIVA
Antes, "Backtest 1 día" siempre corría IFC aunque fueras a operar otra cosa. Ahora prueba la estrategia de tu config: **ifc** → Continuidad vs Reversión (con botones Aplicar C/R por par, como siempre); **facundo** → Directo vs Invertido (informativo: el flag es global, la columna con ● es tu config actual); **nr** → Rebote vs Ruptura; **ifcpro** → su única variante. La tabla de resultados adapta sus columnas automáticamente.

### 4. Servidor multi-hilo
El servidor HTTP pasó de un solo hilo a `ThreadingHTTPServer`: un backtest de 20 pares ya no congela la interfaz — el polling de estado, el log en vivo y los demás botones siguen respondiendo mientras corre (verificado: /api/estado responde en ~7 ms durante un backtest masivo).

### 5. Race condition de NR eliminado (mismo patrón que Facundo)
`_backtest_nr` mutaba `config['nr_modo']` mientras corría — si el bot operaba NR en vivo durante un backtest, podía decidir con el modo equivocado. Ahora el modo se pasa como parámetro explícito al detector (`detectar_numero_redondo(velas, par, modo)`); cero mutación de config, verificado con lector concurrente.

### 6. Verificación de la regla del 3er toque
Batería de 7 casos borde, todos en verde: señal solo con 2+ toques previos de velas distintas y separadas (la entrada es el 3er toque), nivel virgen descartado, un solo toque previo descartado, congestión de velas consecutivas cuenta como UN toque, la vela de rechazo/confirmación no se auto-cuentan, y la exigencia escala si subes `facundo_sr_toques_previos`.

---

## ⭐ RONDA DE ARREGLOS FACUNDO (versión anterior)

Tras el análisis técnico de la implementación, se corrigieron estos problemas. **La inversión de dirección (`facundo_invertir: true`) se MANTIENE tal como la decidió el usuario** — nada de esta ronda cambia esa elección.

### 1. Regla del 3er toque (pedida por el usuario)
La entrada ahora exige que el nivel S/R haya sido tocado por **al menos 2 velas distintas ANTES** de la vela de señal — la entrada es como mínimo el **3er toque** del nivel. Config: `facundo_sr_toques_previos: 2` (subir a 3 = entrada en el 4to toque, etc.). Aplica a las tres variantes: rechazo, rompimiento (el nivel roto debe ser un nivel probado) y lateral (el techo/piso del rango debe haber sido tocado 2+ veces).

### 2. Toques de velas DISTINTAS (bug corregido)
Antes, el máximo y el mínimo de UNA MISMA vela contaban como 2 "toques": una vela pequeña de 1 pip creaba sola un "nivel importante" (medimos 18 niveles en 50 velas tranquilas — el gráfico estaba empapelado de falsos S/R, y por eso el bot disparaba ~8 señales/hora cuando el trader del video hace 2-4 entradas por sesión). Ahora una vela aporta máximo 1 toque por nivel. Menos señales, niveles reales.

### 3. Filtro EMA sobre la dirección FINAL (bug corregido)
El filtro de tendencia validaba la dirección ORIGINAL del detector y la inversión se aplicaba DESPUÉS: resultado medido, el **100% de las entradas ejecutadas iban contra la tendencia EMA** — el filtro protegía exactamente al revés. Ahora el filtro se aplica en `_facundo_decidir()` sobre la dirección que de verdad se ejecuta (post-inversión). Con `facundo_invertir: true`, las entradas invertidas ahora solo pasan si van a favor (o neutral) de la tendencia.

### 4. Race condition del backtest (bug corregido)
`_backtest_facundo` mutaba `config["facundo_modo"]` y `config["facundo_invertir"]` globalmente mientras corría: si el bot operaba Facundo en vivo durante un backtest, una señal podía ejecutarse con la dirección OPUESTA a la configurada (93% de lecturas concurrentes veían config pisada en la prueba). Ahora vivo y backtest usan el mismo núcleo puro `_facundo_decidir(velas, modo, invertir)` con parámetros explícitos — cero mutación de config, y el backtest mide EXACTAMENTE la misma lógica que opera el bot (filtro EMA e inversión incluidos).

### 5. Filtro de payout en vivo (config muerta → funcional, desactivado por defecto)
`facundo_payout_min` existía pero no se usaba en ninguna parte. Ahora el mecanismo funciona de verdad, pero viene en `0` = **DESACTIVADO** (decisión del usuario: su bróker no paga 90%, así que el bot acepta cualquier payout). Si algún día quieres la regla del video, pon el % mínimo en `facundo_payout_min` (ej. 87): las señales de pares por debajo se descartarán con log claro `[FACUNDO] señal descartada: payout X% < mínimo`. El payout se cachea (refresco cada 5 min desde el loop principal, fuera de la ventana de entrada) y es fail-open: si el bróker no reporta el payout de un par, se avisa una vez y se opera igual. Recordatorio de matemática del instrumento: payout más bajo = break-even más alto (87% → 53.5%, 80% → 55.6%, 70% → 58.8%).

### 6. `facundo_sr_lookback` ahora funciona
El valor 80 se pasaba a `detectar_sr()`, que por dentro volvía a recortar a `sr_lookback` (50) — nunca tuvo efecto. El detector de niveles propio de Facundo (`_facundo_sr_niveles`) aplica el lookback directamente.

### 7. Prioridad del coordinador por estrategia
`loop_par` calculaba la prioridad de TODAS las señales con `detectar_ifc`, aunque la estrategia activa fuera Facundo (la prioridad caía a un valor plano y el desempate entre pares era ciego). Ahora despacha según la estrategia activa: Facundo usa el cuerpo de confirmación + bono por toques del nivel (+3 por toque sobre el mínimo, tope +15); IFC-Pro usa su propio detector.

### 8. `validacion_oos.py` reparado y con Facundo
Referenciaba `_backtest_chart` y `_backtest_mhi`, funciones que ya no existen en este bot (crasheaba con `--estrategia chart|mhi`). Se retiraron esas opciones y se añadió `--estrategia facundo`: valida las **8 variantes** (4 modos × directo/invertido), elige la mejor en TRAIN y la mide en TEST — que es exactamente la decisión directo-vs-invertido que hay que tomar con datos en vez de con una sesión corta.

### Config: cambios de claves
- **Nueva**: `facundo_sr_toques_previos: 2` (regla del 3er toque)
- **Retirada**: `facundo_sr_toques_min` (el conteo viejo estaba roto; si sigue en tu config.json es inofensiva, nadie la lee)

### Qué esperar al operar
Menos señales que antes (niveles reales + 3er toque + EMA coherente filtran mucho más). Antes de la próxima sesión, corre el backtest de 8 filas sobre tus pares (3000+ velas) y `python validacion_oos.py --estrategia facundo`: con las señales nuevas, la comparación DIRECTO vs INVERTIDO parte de cero — decide con esa tabla, no con la sesión anterior (aquellas señales ya no existen con estos filtros).

---

Esta versión incluye mejoras de **seguridad**, **fiabilidad**, **gestión de riesgo** y **validación estadística** sobre el bot original, más la **estrategia FACUNDO** (price action sobre S/R) añadida en esta iteración. Los archivos del bot original están intactos salvo donde se indica; los cambios están marcados con comentarios `SECURITY:`, `RELIABILITY:`, `RACE-FIX:`, `SAFETY:` o `WATCHDOG:`.

## Archivos nuevos

- **`validacion_oos.py`** — Script standalone de validación out-of-sample. Corre antes de operar para saber si la auto-calibración del bot es borde real o curve-fitting. Uso:
  ```
  python validacion_oos.py
  python validacion_oos.py --pares EURUSD-OTC,GBPUSD-OTC --velas 6000
  ```

## Novedad: estrategia FACUNDO (price action sobre líneas importantes)

Añadida a partir del video "Táctica 99% EFECTIVA para GANAR en OTC" de Facundo Contreras. Es price action puro sobre soportes y resistencias detectados automáticamente, con tres variantes seleccionables:

- **`rechazo`** — vela deja mecha de rechazo (≥30% del rango) en un S/R + la siguiente vela confirma con cuerpo en la dirección del rechazo. PUT en resistencia, CALL en soporte.
- **`rompimiento`** — vela cierra más allá del S/R con cuerpo ≥60% y margen ≥3 pips + la siguiente vela confirma en la misma dirección. Entrada a favor del rompimiento.
- **`lateral`** — identifica rango en las últimas 30 velas (techo/piso); si la vela toca un extremo con mecha y la siguiente confirma, entra a favor del rebote.
- **`ambos`** (default) — rechazo + rompimiento a la vez; si ambos disparan, gana el de mayor cuerpo de confirmación.

### Funciones nuevas en `bot.py`
- `_facundo_sr(velas)` — wrapper de S/R con la ventana de Facundo.
- `_facundo_filtro_ema(direccion, velas)` — filtro de tendencia opcional.
- `detectar_facundo_rechazo(velas)` → `{"dir", "nivel", "tipo":"rechazo", "cuerpo_conf"}` o `None`.
- `detectar_facundo_rompimiento(velas)` → `{"dir", "nivel", "tipo":"rompimiento", "cuerpo_conf"}` o `None`.
- `detectar_facundo_lateral(velas)` → `{"dir", "nivel", "tipo":"lateral", "cuerpo_conf"}` o `None`.
- `_analizar_facundo(velas, par=None)` → `("BUY"/"SELL"/"NONE", descripción)`.
- `_backtest_facundo(velas, modo, ventana=None)` — backtest de la variante pedida.

### Dispatcher y backtest general
- `analizar()` ahora enruta `"facundo"` a `_analizar_facundo`.
- `backtest_estrategia()` ahora corre los 4 modos de Facundo (rechazo / rompimiento / lateral / ambos) para comparar cuál rinde mejor en cada par.

### UI
- `ui/index.html` — añadida opción "Facundo (price action en S/R)" al `<select>` de estrategia.
- `servidor.py` (página de backtest) — añadida misma opción y pista actualizada.

### Config keys nuevas (en `config.json`)
```json
{
  "facundo_modo":                  "ambos",
  "facundo_sr_lookback":           80,
  "facundo_sr_toques_min":         2,
  "facundo_sr_zone_pips":          0.0003,
  "facundo_mecha_min_ratio":       0.30,
  "facundo_confirm_cuerpo_min":    0.40,
  "facundo_romp_cuerpo_min":       0.60,
  "facundo_romp_margen_pips":      0.0003,
  "facundo_lateral_lookback":      30,
  "facundo_lateral_tolerancia_pips": 0.0005,
  "facundo_lateral_max_amplitud_x":  8,
  "facundo_usar_ema":              true,
  "facundo_payout_min":            90,
  "facundo_invertir":              true
}
```

### ⚠️ Inversión de dirección (importante)

Tras la primera ronda de pruebas en vivo (11 operaciones, 9 perdidas), se detectó que las entradas iban "al revés". Se añadió el flag `facundo_invertir` (default `true`) que invierte la dirección de TODAS las señales Facundo sin tocar los detectores:

| Situación detectada | Original (perdió 9/11) | Invertido (default actual) |
|---|---|---|
| Mecha superior en resistencia | PUT | **CALL** |
| Mecha inferior en soporte | CALL | **PUT** |
| Rompe resistencia arriba | CALL | **PUT** (apuesta a falso rompimiento) |
| Rompe soporte abajo | PUT | **CALL** (apuesta a falso rompimiento) |
| Toca techo del rango | PUT | **CALL** |
| Toca piso del rango | CALL | **PUT** |

**Cómo saber cuál te conviene**: en la pestaña Backtest, elige "Facundo" y corre sobre tus pares. Ahora verás 8 filas (4 modos × 2 versiones DIRECTO/INVERTIDO). Marca con `(config actual)` la que el bot usará en vivo. La que supere el break-even (53.5%) en TU histórico es la que debes dejar activa.

**Para volver al modo directo**: pon `facundo_invertir: false` en `config.json`.

**Advertencia estadística**: 11 operaciones es muestra chica. La inversión puede ser varianza, no borde real. Corre el backtest sobre 3000+ velas y varios pares antes de creer que la versión invertida es la correcta. Si la invertida tampoco supera 53.5% en el histórico, **no operates Facundo en real**.

### Cómo usar la estrategia Facundo
1. En la UI, selecciona **"Facundo (price action en S/R)"** como estrategia de la sesión.
2. En `config.json`, ajusta `facundo_modo` a la variante que mejor rinda en tu par (corre el backtest primero).
3. **Antes de operar en real**: en la pestaña Backtest, elige "Facundo" y corre sobre tus pares con ≥3000 velas. Solo opera los pares donde alguna variante supere el break-even (53.5% con payout 87%).
4. Idealmente combina con `python validacion_oos.py` para evitar curve-fitting.
5. Si un par no supera 53.5% en TEST, **no lo operes con Facundo en real**.

### Advertencias honestas
- El "99% efectivo" del video es marketing. La estrategia es price-action clásico (S/R rejection + breakout + range), razonable pero no mágica.
- Convertir estrategia discrecional → algoritmo pierde parte del contexto visual que el trader usa. Habrá falsos positivos.
- En OTC con payout 87% necesitas ≥53.5% de winrate solo para empatar. Si tu bróker paga 90%+, el break-even baja a ~52.6%.
- **Recomendación**: opera en PRACTICE al menos 1 mes después de pasar la validación out-of-sample antes de pensar en dinero real.

## Mejoras anteriores aplicadas

### A. Validación out-of-sample (`validacion_oos.py`)
La auto-calibración original elige el "mejor modo" por par sobre los últimos 4000 velas y lo opera en vivo — eso es curve-fitting. El script nuevo parte el histórico en 70% train / 30% test, elige modo en train y lo mide en test (out-of-sample). Solo si un par supera el break-even (53.5%) en TEST hay señal de borde real.

### B. Seguridad (`servidor.py`, `app.py`, `iniciar.bat`, `bot.py`)
- **CSRF token**: todos los POST requieren un token de sesión que se sirve solo desde `/api/estado`. Una web maliciosa que visites no puede llamar a `/api/iniciar`, `/api/guardar_config`, etc.
- **Password enmascarada**: `/api/config` ya no devuelve la contraseña. Solo devuelve `has_password: true/false`. La UI pide re-escribirla para iniciar.
- **CORS limitado**: era `Access-Control-Allow-Origin: *`, ahora `http://127.0.0.1:5000`.
- **`app.py` (legacy) ahora en `127.0.0.1`** en vez de `0.0.0.0` (no expone credenciales a la LAN).
- **Pin de versión** en `iniciar.bat`: `pip install iqoptionapi==1.3.0` (mitiga supply-chain attack).
- **`set_config` no borra password vacío** si la UI envía campo vacío (no rompe el config guardado).

### C. Fiabilidad (`bot.py`)
- **Timeouts en `check_win_v3`, `get_balance`**: antes colgaban el hilo del par para siempre. Ahora `_con_timeout(15s)` con 3 reintentos. Si todo falla, se cuenta como empate neutro y el hilo sigue vivo.
- **Watchdog de hilos por par**: cada `loop_par` anota un heartbeat por iteración. Un hilo watchdog revisa cada 60s; si un par lleva >5 min sin avanzar, marca `⏰ ATASCADO` en el estado y fuerza reconexión limpia del socket.
- **Exposición en vuelo**: el estado ahora trackea `exposicion_en_vuelo` (suma de montos abiertos).

### D. Position sizing (`bot.py`)
- **`_calcular_monto_base(par)`**: si `position_sizing_activa=True`, calcula monto con Kelly fraccional:
  `monto = clamp(balance × riesgo% × f_kelly, min, max)` donde `f_kelly = max(0, (p×payout − (1−p))/payout) × kelly_fraction` (1/4 Kelly por defecto).
- **`_limitar_martingala`**: limita la exposición total de una racha de martingala. Si seguir duplicando excedería `martingala_max_exposicion_usd` (default $10), resetea a monto base en vez de seguir.
- **`exposicion_max_pct`**: si la suma de montos en vuelo supera X% del balance, las nuevas entradas se rechazan con log `[EXPOSICION]`.

### E. UI conectada a los nuevos campos (`ui/index.html`)
Los campos "Riesgo %", "Mín $", "Máx $" **antes se guardaban pero el bot los ignoraba**. Ahora se mapean a `position_sizing_riesgo_pct`, `position_sizing_min`, `position_sizing_max` y activan automáticamente el position sizing. La UI ahora muestra "(Kelly fraccional)" y una nota explicativa.

### F. Race condition en `_soy_ganador` (`bot.py`)
Antes, el primer hilo que llegaba al `seg 00` congelaba al ganador con `max(candidatos)` — pero si otro con mayor prioridad llegaba 0.4s después, quedaba descartado injustamente. Ahora se espera un breve margen (200ms) para que se registren todos los candidatos antes de decidir.

## Cómo usar

1. **Antes de operar con dinero real**, corre la validación:
   ```
   python validacion_oos.py
   ```
   Si en TEST ningún par supera 53.5%, **NO operes con dinero real** — era curve-fitting.

2. **Arranca como siempre**: doble-click en `iniciar.bat` (Windows) o `python servidor.py`. Abre `http://localhost:5000`.

3. **Re-escribe tu password** la primera vez (ya no se precarga por seguridad).

4. **Activa position sizing** dejando "Riesgo %" > 0 (default 2). El monto se calcula solo según el balance.

5. **Si activas martingala**, el límite `martingala_max_exposicion_usd=$10` evita que una racha de 3 niveles te comprometa más de $10 en total. Ajústalo en `config.json`.

6. **Para usar la estrategia Facundo**: elige "Facundo" en el `<select>` de estrategia de la sesión, ajusta `facundo_modo` en `config.json`, y corre el Backtest sobre tus pares antes de operar en real.

## Tests realizados

- ✅ Sintaxis OK en `bot.py`, `servidor.py`, `app.py`, `validacion_oos.py`
- ✅ `bot` importa sin errores
- ✅ Position sizing: balance=$5000, p=0.60 → monto=$3.51 (correcto)
- ✅ Martingala limit: `8+5 > 10` → reset (correcto)
- ✅ Race condition: PAR-B (prioridad 70) gana aunque PAR-A (50) se registre primero
- ✅ `set_config` no borra password vacío
- ✅ Servidor HTTP: POST sin CSRF → 403, con CSRF → 200
- ✅ `/api/config` no devuelve password
- ✅ Backtest con datos aleatorios da ~50% wr (sin borde, como debe ser)
- ✅ Detector Facundo RECHAZO: detecta PUT en resistencia correctamente
- ✅ Detector Facundo ROMPIMIENTO: detecta CALL al romper resistencia correctamente
- ✅ Detector Facundo LATERAL: detecta PUT en techo del rango correctamente
- ✅ `_analizar_facundo` despacha bien en los 4 modos (rechazo/rompimiento/lateral/ambos)
- ✅ `_backtest_facundo` corre sin error y devuelve estructura esperada
- ✅ `analizar()` enruta `"facundo"` correctamente sin romper `"ifc"` ni `"ifcpro"`

## Lo que NO se puede arreglar con código

- **OTC es un feed sintético del bróker**. Cualquier "borde" es contra el algoritmo del bróker, que puede cambiar cuando quiera.
- **Binarias con payout 87% exigen 53.5% de winrate** solo para empatar.
- **Recomendación**: opera en PRACTICE al menos 1 mes después de pasar la validación out-of-sample antes de pensar en dinero real.
