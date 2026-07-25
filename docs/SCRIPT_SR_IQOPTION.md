# Indicador de Soportes y Resistencias por Fuerza (IQ Option)

Script en **Lua** (Quadcode Script, el lenguaje de indicadores de IQ Option) que
dibuja sobre el gráfico los soportes y resistencias vivos y **los colorea según
su fuerza**.

Archivo: [`scripts_iqoption/soportes_resistencias_fuerza.lua`](../scripts_iqoption/soportes_resistencias_fuerza.lua)

| Fuerza del nivel | Toques | Color | Grosor |
|---|---|---|---|
| Muy fuerte | 4 o más | 🔴 Rojo `#FF3B30` | 3 px |
| Medio | 3 | 🟢 Verde `#25E154` | 2 px |
| Débil | 1 – 2 | 🟡 Amarillo `#FFD400` | 1 px |

Los tres colores, los grosores y los umbrales de toques se cambian desde los
ajustes del indicador — no hace falta tocar el código.

---

## 1) Cómo instalarlo en IQ Option

1. Abre IQ Option (web o app de escritorio) y entra en un gráfico de **velas**.
2. Abajo a la izquierda pulsa **Indicadores** → pestaña **Scripts**
   (en algunas versiones: *Mis indicadores* / *Editor de scripts*).
3. Pulsa **Crear script** (o **+ Nuevo**).
4. Abre `scripts_iqoption/soportes_resistencias_fuerza.lua`, **copia TODO** el
   contenido y pégalo en el editor (borra antes el ejemplo que trae).
5. **Guardar** → **Aplicar**. El indicador aparece como
   **“S/R por Fuerza (JPH)”** dibujado encima de las velas.

> El editor de scripts está disponible en la web y en la app de escritorio.
> En la app de móvil se pueden **usar** los scripts guardados, pero no crearlos.

Para cambiar ajustes: icono del **engranaje** junto al nombre del indicador.

---

## 2) Cómo decide la fuerza de un nivel

Es la misma lógica que usa el bot en `bot.py` (`_facundo_sr_niveles`), llevada
al gráfico:

1. **Nace en un pivote confirmado.** Un máximo (resistencia) o mínimo (soporte)
   que tiene `izq` velas a la izquierda y `der` velas a la derecha más bajas /
   más altas que él. Como se exige la confirmación por la derecha, **el nivel no
   se repinta**: la línea empieza justo donde el pivote quedó confirmado.
2. **Cada retorno del precio a la zona suma 1 toque.** La zona es
   `± ancho_zona × ATR` alrededor del precio del nivel.
3. **Una congestión no cuenta diez veces.** Para que un toque cuente como nuevo
   tienen que haber pasado al menos `sep` velas desde el toque anterior: si el
   precio se queda pegado al nivel, sigue siendo **un** toque.
4. **Un rechazo con mecha larga vale más.** Si la vela deja una mecha contra el
   nivel de al menos `mecha_k` del rango de la vela y cierra del lado correcto,
   suma puntos extra (por defecto +1).
5. **Ruptura.** Si el precio **cierra** más allá del nivel con margen
   (`rup_k × ATR`), el nivel se da por roto y su línea se corta. Con
   *“Nivel roto cambia de rol”* activado, una resistencia rota reaparece como
   soporte conservando su fuerza (y al revés).
6. **Caducidad.** Un nivel que lleva `vida` velas sin ser tocado se borra para
   dejar sitio a niveles vigentes.

Se vigilan hasta **6 resistencias y 6 soportes** a la vez. Cuando no quedan
huecos, un nivel nuevo solo entra si es **más fuerte** que el más flojo que haya
en pantalla (los toques se penalizan por el tiempo que llevan sin tocarse).

---

## 3) Ajustes

| Ajuste | Por defecto | Para qué sirve |
|---|---|---|
| Velas a la izquierda del pivote | 3 | Cuántas velas debe dominar el pivote por la izquierda. Más alto = menos niveles, más relevantes. |
| Velas a la derecha (confirmación) | 3 | Velas de confirmación. Es el retraso con el que aparece la línea. |
| Período ATR | 14 | Volatilidad de referencia para el ancho de la zona. |
| Ancho de zona del nivel (x ATR) | 0.35 | Cuánto margen alrededor del precio cuenta como “toque”. Súbelo en pares muy volátiles / OTC. |
| Velas mínimas entre toques | 3 | Evita que una congestión infle la fuerza. |
| Caducidad sin toques | 400 | Velas sin toques tras las cuales el nivel se borra. |
| Ruptura: cierre más allá (x ATR) | 0.30 | Margen que exige el cierre para dar el nivel por roto. 0 = cualquier cierre al otro lado. |
| Nivel roto cambia de rol | Sí | Resistencia rota → soporte (y al revés). |
| Máximo de niveles por lado | 6 | Baja a 2–3 si quieres el gráfico más limpio. |
| Toques mínimos para dibujar | 1 | Ponlo en 2 o 3 para ver **solo** niveles ya probados. |
| Toques para nivel MEDIO | 3 | Umbral del color verde. |
| Toques para nivel MUY FUERTE | 4 | Umbral del color rojo. |
| Mecha de rechazo (x rango) | 0.5 | Qué mecha se considera rechazo de verdad. |
| Puntos extra por rechazo con mecha | 1 | 0 = todos los toques valen igual. |
| Extender niveles a todo el gráfico | No | Dibuja además una línea horizontal completa con la etiqueta del precio. |
| Colores por fuerza | rojo / verde / amarillo | Colores y grosores de cada tramo de fuerza. |

**Sugerencias de uso**

- *Binarias de 1–5 min en OTC*: `izq/der = 2`, `ancho de zona = 0.5`,
  `toques mínimos = 2` (solo niveles ya probados).
- *Gráficos de 15 min o más*: valores por defecto, o `izq/der = 4` para quedarte
  con la estructura grande.
- Si ves demasiadas líneas: sube *toques mínimos* a 2–3 y baja
  *máximo de niveles por lado*.

---

## 4) Detalles que conviene saber

- **La línea empieza donde el nivel se confirma**, no en el pivote original.
  Es el precio de no repintar: lo que ves en el pasado es lo que el indicador
  veía en ese momento.
- **El color se aplica vela a vela.** Cuando un nivel se refuerza, el tramo
  nuevo de la línea cambia a verde o rojo; el tramo antiguo conserva el color
  que tenía cuando se dibujó. Así se ve *cuándo* el nivel ganó fuerza. Si
  prefieres la línea entera del color actual, activa
  *“Extender niveles a todo el gráfico”*.
- El precio del nivel se **promedia** con cada toque nuevo (igual que el bot),
  así que la línea puede ajustarse unos pips al recibir un toque.
- El indicador no lanza señales de compra/venta: solo marca la estructura.

---

## 5) Pruebas

El script se validó fuera de la plataforma con un emulador del motor de
Quadcode Script (ejecución vela a vela, `make_series`, `get_value`, `highest` /
`lowest`, `plot`):

```bash
sudo apt-get install -y lua5.3          # una sola vez
cd scripts_iqoption/pruebas
lua5.3 test_sr.lua
```

Escenarios cubiertos: rango con rebotes repetidos (comprueba el paso
amarillo → verde → rojo, la ruptura y el cambio de rol), paseo aleatorio de 800
velas, tendencia con retrocesos, mercado plano (ATR cero), gráfico con muy pocas
velas e índices de precio alto.
