# Estrategia SINTETICO — especificación completa

Estrategia dedicada a los **índices sintéticos de Deriv**, incluida en el bot como
`estrategia: "sintetico"`. No recicla lógica de forex: usa la **estructura
matemática publicada** de cada índice. Elige el motor automáticamente según el
símbolo.

---

## Motor 1 — SPIKE-RIDE (Boom y Crash) · el principal

**Cómo funcionan esos índices:** Crash 500 sube de forma sostenida y, en promedio
1 de cada 500 ticks, sufre una caída brusca (spike). Boom es el espejo (baja y
tiene spikes alcistas). Esto no es una opinión de mercado: es la definición del
producto.

**Qué hace el bot:**
- **CRASH\*** → opera **CALL** (el precio sube entre caídas).
- **BOOM\*** → opera **PUT** (el precio baja entre subidas).
- **Filtro post-spike:** si en las últimas `sintetico_post_spike_velas` velas
  (por defecto 3) hubo un spike —vela con rango > `sintetico_spike_factor` ×
  la mediana de rangos (por defecto 5×)— **no entra** y espera.
- Expiración recomendada: **1 minuto**.

### Resultados medidos (backtest sobre 7 días de datos generados según la especificación del índice, ~10.000 velas M1 por índice)

| Índice | Winrate medido | Teórico estructural | Peor racha |
|---|---|---|---|
| CRASH 500 | **89.5%** | 88.7% | 3 |
| CRASH 1000 | **94.5%** | 94.2% | 2 |
| BOOM 500 | **88.8%** | 88.7% | 3 |
| BOOM 1000 | **94.5%** | 94.2% | 3 |

Lo medido coincide con lo teórico → la estrategia captura la estructura real
del índice, no un golpe de suerte del backtest.

### ⚠️ La letra pequeña que DEBES entender (honestidad primero)

Un winrate de 89% **no garantiza ganancia**: Deriv pone el precio del contrato
sabiendo esa estructura, así que ir a favor de la deriva **paga poco** (payouts
típicos de 5–15%, no 90%). El break-even depende del payout:

| Payout del contrato | Winrate necesario para no perder |
|---|---|
| 95% | 51.3% |
| 20% | 83.3% |
| 12% | 89.3% |
| 10% | 90.9% |

**Por eso el bot trae un guard de payout** (`deriv_payout_min`): antes de cada
entrada consulta el payout real del contrato y, si está por debajo del mínimo
que configures, **descarta la operación** y lo deja en el log. Valores
recomendados de partida:

- CRASH/BOOM 500 (WR ≈ 89%) → `deriv_payout_min: 13`
- CRASH/BOOM 1000 (WR ≈ 94.5%) → `deriv_payout_min: 7`

Con el guard activo, el bot solo entra cuando el precio del contrato deja
margen frente al winrate medido. Sin margen, no operar **es** la decisión
correcta.

---

## Motor 2 — REVERSIÓN Z-SCORE (Volatility R_* / 1HZ*)

Entra **contra** extremos estadísticos: cuando el cierre se aleja más de
`sintetico_z_umbral` desviaciones (por defecto 1.8) de la media de
`sintetico_z_periodo` velas (por defecto 20) **y** hay una racha de
`sintetico_racha_min` velas del mismo color (por defecto 3).

### Resultado medido — y la verdad

| Índice | REVERSIÓN | CONTINUIDAD (invertida) |
|---|---|---|
| R_75 | 49.4% | 50.6% |
| R_100 | 49.8% | 50.2% |

**≈50% en ambas direcciones.** Es el resultado esperado: los Volatility son
movimiento browniano puro y **no tienen dirección explotable por velas**; con
payout ~95% el break-even es 51.3% y ninguna variante lo supera de forma
estable. Este motor existe para que lo **midas tú con velas reales** desde la
página de backtest — si tu medición real tampoco supera el break-even
(esperable), **no operes Volatility direccional**: opera Boom/Crash con el
guard de payout.

---

## Configuración recomendada para empezar (DEMO)

```json
{
  "broker": "deriv",
  "estrategia": "sintetico",
  "pares_deriv": ["CRASH500", "CRASH1000", "BOOM500", "BOOM1000"],
  "modo": "PRACTICE",
  "expiracion": 1,
  "deriv_payout_min": 13,
  "martingala_activa": false,
  "position_sizing_activa": true,
  "position_sizing_riesgo_pct": 1,
  "max_perdidas_seguidas": 3,
  "max_perdidas_dia": 5
}
```

Reglas de riesgo **no negociables** en sintéticos:

1. **Martingala APAGADA.** Con WR 89% la peor racha medida fue 3, pero los
   spikes son independientes entre sí: puede venir una racha peor y la
   martingala la convierte en ruina.
2. **Riesgo máximo 1% por operación.**
3. **Primero DEMO**, mínimo 100–200 operaciones, y compara el winrate real y
   el payout real contra esta tabla antes de pensar en real.

## Cómo re-verificar con datos reales (1 clic, desde tu casa)

1. Abre la app → selector **Bróker: Deriv** (token configurado) → **Iniciar bot**
   o directamente la página **/backtest**.
2. Estrategia: **SINTETICO**, Par: `CRASH500` (o el que quieras), 5000+ velas.
3. El backtest corre sobre **velas reales de tu cuenta Deriv** y te muestra la
   misma tabla (estrategia con filtros vs línea base). Los números deben salir
   en la zona de la tabla de arriba; si no, pásame el resultado por el chat.

## Parámetros (config.json)

| Clave | Default | Qué hace |
|---|---|---|
| `sintetico_spike_factor` | 5.0 | Rango > factor×mediana ⇒ es spike |
| `sintetico_post_spike_velas` | 3 | Velas de espera tras un spike |
| `sintetico_z_periodo` | 20 | Ventana del z-score (Volatility) |
| `sintetico_z_umbral` | 1.8 | \|z\| mínimo para señal |
| `sintetico_racha_min` | 3 | Racha mínima del mismo color |
| `deriv_payout_min` | 0 | Payout mínimo del contrato en % (0 = solo registrar) |
