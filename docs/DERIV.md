# Operar índices sintéticos con Deriv

Tu bot ahora puede cambiar de bróker desde el panel:

- **IQ Option** → forex / pares OTC (como siempre).
- **Deriv** → índices sintéticos (Volatility, Boom & Crash, Step, Jump).

Cambias de uno a otro cuando quieras con el selector **Bróker** arriba del panel.
Cada bróker guarda sus propias credenciales y su propia lista de pares.

---

## 1) Crear cuenta y token en Deriv

> **Nota (2025+):** Deriv cambió su sistema de tokens. Ahora genera **Personal
> Access Tokens** que empiezan por **`pat_`** y tienen fecha de vencimiento
> (máx. 90 días). El bot ya está actualizado a este sistema nuevo.

1. Crea tu cuenta en https://deriv.com (si aún no la tienes).
2. Entra a **https://app.deriv.com/account/api-token**.
3. Rellena:
   - **Nombre del token:** por ejemplo `jphbot`.
   - **Permisos:** marca **Comercio (Trade)**. (Con eso basta para operar.)
   - **Vencimiento:** el que quieras (máx. 90 días; cuando caduque, creas otro).
4. Pulsa **Crear token** y **copia** el token (`pat_...`). Deriv lo muestra una
   sola vez, así que guárdalo.
5. **No hace falta elegir demo o real al crear el token:** el bot elige
   automáticamente tu cuenta **demo** o **real** según el **Modo** que pongas en
   el panel, y se **niega a operar en real si elegiste modo Práctica** (y al
   revés). Solo asegúrate de tener tu cuenta demo activa en Deriv.

---

## 2) Configurar el bot

En el panel:

1. En **Bróker**, elige **Deriv (índices sintéticos)**.
2. Pega tu **Token de API**.
3. **App ID**: deja `1089` (el público de pruebas de Deriv) salvo que tengas uno propio.
4. **Índices sintéticos**: escribe los que quieras operar, separados por coma. Ejemplos:
   - Volatility: `R_10, R_25, R_50, R_75, R_100`
   - Volatility 1s: `1HZ10V, 1HZ25V, 1HZ50V, 1HZ75V, 1HZ100V`
   - Boom / Crash: `BOOM500, BOOM1000, CRASH500, CRASH1000`
   - Step / Jump: `STPRNG, JD10, JD25, JD50, JD75, JD100`
5. **Modo**: deja **Demo** mientras pruebas.
6. Guarda la configuración y dale a **Iniciar bot**.

---

## 3) Probar en DEMO primero (obligatorio)

Este conector de Deriv es nuevo y **debe validarse en demo** antes de arriesgar dinero:

- Déjalo operar un rato en modo **Demo**.
- Si algo falla, copia el **log** del panel y pásamelo por el chat: con eso lo afino.
- Pasa a **Real** solo cuando lo veas operar bien en demo.

---

## Diferencias importantes frente a forex

- Los sintéticos son **ruido con volatilidad constante, 24/7**. No hay noticias ni
  soportes/resistencias "reales". Por eso, en Deriv el bot **no usa** el escáner ni la
  auto-calibración de forex: opera directamente la lista de índices que configuraste.
- Las estrategias pensadas para forex (números redondos, S/R) **pueden no rendir igual**
  en sintéticos. El bot incluye una estrategia dedicada: **SINTETICO** — spike-ride en
  Boom/Crash y reversión z-score en Volatility. Especificación completa, resultados
  medidos y configuración recomendada en **`docs/ESTRATEGIA_SINTETICOS.md`**.
- ⚠️ **La martingala es especialmente peligrosa en sintéticos** (pueden encadenar
  rachas largas). Úsala con mucha prudencia o déjala apagada.

## Nota técnica

El conector de Deriv (`brokers/conector_deriv.py`) expone la misma interfaz que el bot
ya usaba con IQ Option (`get_candles`, `buy`, `check_win_v3`, etc.), así que el resto
del bot —panel, estadísticas, historial, auto-actualización— funciona igual con ambos
brókers. El bróker activo se elige en `config["broker"]` (`"iq"` o `"deriv"`).
