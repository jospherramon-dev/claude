# Changelog — Ajustes v9.1.2 (13 julio 2026)

Esta versión aplica cambios **exclusivamente a la estrategia Facundo** basados en el análisis de una entrada ganadora de referencia (3 toques en soporte, entrada en el 3er toque a favor de tendencia) y en las reglas explícitas del usuario.

---

## Filosofía de esta versión

**Decisiones explícitas del usuario:**
- Mantener mínimo 2 toques previos → entrada en 3er toque ✅ (ya existía).
- Si existe una entrada con 3 toques previos (4to toque), tomarla como **prioridad** sobre el 3er toque.
- Tomar como referencia **el nivel con MÁS toques** cuando hay múltiples niveles válidos.
- No tocar el filtro de tendencia EMA por ahora (queda para estudiarlo).

**Lo que NO se toca:**
- Estrategia IFC, IFC-PRO, NR (cambios solo a Facundo).
- Pares operados (siguen todos los 21 candidatos OTC).
- Horarios (sin bloqueos horarios).
- Sizing y martingala (siguen igual).
- Filtro EMA (sigue con `facundo_usar_ema: True`).

---

## Cambio #1 — Seleccionar el nivel con MÁS toques previos

**Archivos:** `bot.py` líneas 2268-2313 (`detectar_facundo_rechazo`) y 2346-2392 (`detectar_facundo_rompimiento`)

**Comportamiento ANTERIOR:**
Cuando la vela de rechazo/rompimiento tocaba la zona de un nivel S/R válido, el bot hacía `break` en el **primer** nivel que coincidía. Si existían 2 niveles válidos en la misma zona (uno con 2 toques prev = 3er toque, otro con 3 toques prev = 4to toque), el bot tomaba el primero que encontraba, perdiendo la oportunidad de operar el nivel más fuerte.

**Comportamiento NUEVO:**
El bot recorre **TODOS** los niveles válidos que coinciden con la mecha/extremo de ruptura, los guarda en una lista `candidatos`, y selecciona el de **MAYOR cantidad de toques previos** con `max(candidatos, key=lambda c: c[2])`. En caso de empate, queda el primero encontrado (orden de `sr_levels`).

**Ejemplo concreto (caso de la imagen de referencia):**
- Mecha inferior toca zona donde hay DOS niveles válidos:
  - Nivel A en 1.0850 con 2 toques previos → entrada sería 3er toque
  - Nivel B en 1.0848 con 3 toques previos → entrada sería 4to toque
- **Antes:** el bot tomaba el Nivel A (primero encontrado).
- **Ahora:** el bot toma el Nivel B (más toques previos = nivel más fuerte).

**Log de diagnóstico:** Cuando hay múltiples candidatos y se elige uno, el bot imprime en el log:
```
[FACUNDO-RECHAZO] 2 niveles válidos cerca de la mecha. Elegido: CALL @ 1.08480 (3 toques prev). Otros: CALL@1.08500(2)
[FACUNDO-ROMPIMIENTO] 2 niveles rotos. Elegido: PUT @ 1.10000 (4 toques prev). Otros: PUT@1.09980(2)
```
Esto te permite verificar en tiempo real que el cambio funciona.

---

## Cambio #2 — Reforzar el bono por toque extra en prioridad

**Archivo:** `bot.py` líneas 1323-1334 (`_prioridad_senal`)

**Comportamiento ANTERIOR:**
Cuando varios pares señalan en la misma vela M1, el bot solo ejecuta la señal del par con **mayor prioridad**. La fórmula era:
```
prioridad = cuerpo_conf + bono_toques + adx/2
donde bono_toques = min(15, (toques - 3) * 3)
```
Es decir: 3er toque = +0, 4to = +3, 5to = +6, 6to+ = +15.

**Problema:** Con un cuerpo de confirmación típico de 60-80%, un bono de +3 apenas influía. Un 3er toque con cuerpo 70% (prioridad 70+0+7.5 = 77.5) le ganaba a un 4to toque con cuerpo 60% (60+3+7.5 = 70.5).

**Comportamiento NUEVO:**
```
bono_toques = min(30, (toques - 3) * 15)
```
Es decir: 3er toque = +0, 4to = +15, 5to = +30, 6to+ = +30 (tope).

**Ejemplo concreto:**
- Par A: 3er toque, cuerpo conf 70%, ADX 15 → prioridad = 70 + 0 + 7.5 = **77.5**
- Par B: 4to toque, cuerpo conf 60%, ADX 15 → prioridad = 60 + 15 + 7.5 = **82.5** ✓ GANA

Ahora el 4to toque gana la prioridad aunque tenga hasta 15 puntos menos de cuerpo de confirmación. Si la diferencia de cuerpo es mayor a 15, gana el 3er toque (caso en que la confirmación del 4to toque sería muy débil y no conviene operarlo).

**Justificación:** Implementa la regla explícita del usuario: *"si hay una entrada que tiene 3 toques para entrar en el 4to toque, tomar esta como prioridad"*. El 4to toque de un nivel es estadísticamente más confiable que el 3ro (el nivel ha sido probado más veces), por lo que merece una ventaja competitiva clara cuando ambos señalan en la misma vela.

---

## Cambio #3 — Documentación de la regla de toques

**Archivo:** `bot.py` líneas 310-319

Actualicé los comentarios de `facundo_sr_toques_previos` y `facundo_sr_toques_max_previos` para explicar el nuevo comportamiento:

- `facundo_sr_toques_previos: 2` → mínimo 2 previos (entrada en 3er toque). Si hay un nivel con más toques previos, el bot lo prioriza automáticamente.
- `facundo_sr_toques_max_previos: 0` → sin tope, permite 4to, 5to, 6to toque. Si quieres limitar a 3er/4to, poner 3.

---

## Tests de validación

Se ejecutaron 4 tests de lógica (`/home/z/my-project/scripts/test_cambio_facundo.py`):

| Test | Resultado |
|------|-----------|
| Selección de 4to toque sobre 3er toque (misma mecha) | ✓ |
| Funciona con 1 solo candidato | ✓ |
| Empate resuelto (toma el primero encontrado) | ✓ |
| 5to toque elegido sobre 3er toque | ✓ |
| Bono reforzado: 3er=0, 4to=+15, 5to=+30, 6to+=+30 | ✓ |
| 4to toque gana prioridad con cuerpo conf 10pts menor | ✓ |

**Sintaxis Python válida** (verificada con `ast.parse`).

---

## Lo que se mantiene igual (para tu referencia)

| Parámetro | Valor | Línea | Razón |
|-----------|-------|-------|-------|
| `facundo_sr_toques_previos` | 2 | 310 | Mínimo 2 previos (entrada en 3er toque) — regla del usuario |
| `facundo_sr_toques_max_previos` | 0 | 316 | Sin tope, permite 4to/5to/6to toque |
| `facundo_sr_separacion_velas` | 3 | 320 | Velas mínimas entre toques del mismo nivel |
| `facundo_sr_zone_pips` | 0.0003 | 323 | Tolerancia "precio cerca de nivel" |
| `facundo_mecha_min_ratio` | 0.30 | 324 | Mecha >= 30% del rango = rechazo |
| `facundo_confirm_cuerpo_min` | 0.40 | 325 | Cuerpo confirmación >= 40% |
| `facundo_usar_ema` | True | 331 | Filtro de tendencia EMA activo (no se tocó) |
| `facundo_invertir` | True | 351 | Inversión anti-curve-fitting (no se tocó) |
| `facundo_modo` | "ambos" | 308 | Rechazo + rompimiento (no se tocó) |

---

## Tema pendiente: tendencia y soportes vs resistencias

Mencionaste la hipótesis de que la entrada de la imagen es muy buena porque el bot respetó la tendencia y tomó el nivel con más toques (soporte). Sobre esto:

**Lo que YA hace el bot (no necesita cambio):**
- Filtro EMA: si la dirección final de entrada va contra la EMA rápida/lenta, la señal se descarta. La imagen muestra una entrada CALL en tendencia alcista → el filtro la deja pasar.
- La detección de soporte vs resistencia ya es diferenciada: mecha inferior cerca de nivel = CALL (soporte); mecha superior cerca de nivel = PUT (resistencia). La imagen muestra mecha inferior tocando soporte → CALL, que es lo que debe hacer.

**Lo que se podría estudiar después (no aplicado):**
- Hacer el filtro EMA más estricto (por ejemplo, exigir que la EMA rápida esté claramente por encima/debajo de la lenta, no solo que no esté en contra).
- Diferenciar la confianza según tipo de nivel: soporte triple en tendencia alcista > resistencia triple en tendencia bajista > otros.
- Considerar la pendiente de la EMA (no solo la posición relativa) para medir fuerza de tendencia.

Cuando quieras explorar esto, mandame otra imagen de entrada buena y otra de entrada mala y lo analizamos.

---

## Cómo verificar que los cambios están activos

1. **Selección de nivel con más toques:** cuando veas en el log del bot:
   ```
   [FACUNDO-RECHAZO] 2 niveles válidos cerca de la mecha. Elegido: CALL @ 1.08480 (3 toques prev). Otros: CALL@1.08500(2)
   ```
   Significa que el bot detectó múltiples niveles válidos y eligió el de más toques previos.

2. **Prioridad del 4to toque:** cuando varios pares señalen en la misma vela y gane uno con 4to toque, verás en el log:
   ```
   [PRIORIDAD] PAR_A cede esta vela a PAR_B (conf 60 + bono +15 por toque #4 + adx 7 vs conf 70 + bono +0 por toque #3 + adx 7).
   ```

3. **Entradas en general:** la razón de cada señal ahora muestra el número de toque real:
   ```
   COMPRA | FACUNDO-rechazo | nivel 1.08480 (toque #4) | cuerpo conf:65% | entrada vela siguiente
   ```

---

## Compatibilidad

- **config.json existentes:** totalmente compatibles. Los nuevos comportamientos son automáticos (no requieren nuevas claves en config). Si ya tienes un `config.json`, los valores que tengas para `facundo_sr_toques_previos`, `facundo_sr_toques_max_previos`, etc. se respetan.
- **sesion.json existentes:** compatibles.
- **historial_operaciones.csv:** compatible, sin cambios en columnas.

---

## Próximos pasos sugeridos

1. Reemplaza la carpeta `bot_facundo_v9` anterior con esta nueva versión.
2. Reinicia el bot.
3. Operá 2-3 días prestando atención a los logs `[FACUNDO-RECHAZO]` y `[FACUNDO-ROMPIMIENTO]` para validar que el bot está eligiendo niveles con más toques.
4. Cuando veas una entrada buena, mandame la captura con el resultado y la comparamos con el patrón esperado.
5. Si ves que el bot descarta buenos 3eros toques por un 4to toque que pierde, podemos reducir el bono de +15 a +10. Si quieres que el 4to SIEMPRE gane sin importar el cuerpo de confirmación, podemos subirlo a +25.

---

# Changelog previo — Ajustes v9.1.1 (12 julio 2026)

Esta versión previa aplicó ajustes preventivos que **siguen vigentes** en v9.1.2:

## Cambio #5 — Activar `vela_institucional_activa` (vigente)
`vela_institucional_activa: False → True` (línea 269). Bloquea 60 min velas con rango > 3.5× promedio (anti-noticias).

## Cambio #7 — Implementar `exposicion_max_pct` (vigente)
Antes era campo zombie. Ahora limita montos en vuelo al 10% del balance. Lógica en `loop_par` líneas 4275-4299.

## Cambio #8 — Documentar `expiracion` zombie (vigente)
Comentario explicativo en líneas 198-203. La expiración real se controla vía `timeframe`, no `expiracion`.
