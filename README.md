# Bot IFC Auto — App de escritorio

Bot de trading para IQ Option con panel de control. Ahora funciona como una
**aplicación de escritorio con su propia ventana** y se **actualiza sola** desde
este repositorio de GitHub.

---

## 1) Instalar en tu PC (una sola vez)

1. Instala **Python** desde https://www.python.org/downloads/
   → Al instalar, marca la casilla **"Add Python to PATH"**.
2. Descarga este proyecto (botón verde **Code → Download ZIP**) y descomprimelo
   en una carpeta fija, por ejemplo `C:\BotIFCAuto`.
3. Doble clic en **`instalar.bat`**.
   - Instala las dependencias.
   - Crea el acceso directo **"Bot IFC Auto"** en tu Escritorio.

Listo. A partir de ahí abres el bot desde ese ícono del Escritorio: se abre en
su **propia ventana**, no en el navegador.

> El acceso directo usa el logo **JPH.BOT** (`icono.ico`). Si cambias el logo,
> vuelve a ejecutar `instalar.bat` para refrescar el ícono del Escritorio.

> La primera vez, Windows puede pedir instalar el runtime **WebView2** (viene de
> serie en Windows 10/11 actualizados). Si hiciera falta, se descarga de Microsoft.

---

## 2) Actualizaciones automáticas

**Cada vez que abres la app**, comprueba si hay una versión más nueva en este
repositorio. Si la hay, la descarga, la aplica y se reinicia sola — **sin borrar
tu configuración, tu sesión ni tu historial** (todo eso vive en la carpeta
`datos/`, que el actualizador nunca toca).

### Cómo publicar una actualización (tú, desde tu PC)

1. Edita el código que quieras (`bot.py`, `ui/index.html`, etc.).
2. Sube el número en el archivo **`VERSION`** (por ejemplo `9.1.0` → `9.2.0`).
   El actualizador solo actualiza si el número remoto es **mayor** que el local.
3. Haz `commit` y `push` a la rama **`main`** de este repo.

La próxima vez que abras la app en tu PC (o en cualquier PC donde esté
instalada), se actualizará sola a esa versión.

### Si tu repositorio es privado

El actualizador necesita permiso para leerlo. Crea un
[token de GitHub](https://github.com/settings/tokens) con permiso de lectura y
guárdalo en el archivo `datos/token_github.txt` (una sola línea), o en la
variable de entorno `BOT_IFC_GH_TOKEN`. Si el repo es público, no hace falta nada.

---

## 3) Uso diario

- Abre **"Bot IFC Auto"** desde el Escritorio.
- Elige el **Bróker**: **IQ Option** (forex/OTC) o **Deriv** (índices sintéticos).
  Puedes cambiar entre ellos cuando quieras — cada uno guarda sus credenciales.
  - IQ Option: usuario/contraseña.
  - Deriv: token de API (ver **`docs/DERIV.md`** para la guía paso a paso).
- Elige modo (PRACTICE/REAL) y dale a **Iniciar bot**.
- Herramientas incluidas en el panel: diagnóstico de pares, backtest y
  estadísticas (con exportación a Excel/CSV).

## Dónde se guardan tus datos

Todo lo tuyo vive en la carpeta **`datos/`** dentro de la app:

| Archivo | Qué es |
|---|---|
| `config.json` | Tu configuración y credenciales |
| `sesion.json` | La sesión del día en curso |
| `historial_operaciones.csv` | Historial permanente de operaciones |

Esa carpeta **no se sube a GitHub** ni se pierde al actualizar.

---

## Archivos del proyecto

| Archivo | Función |
|---|---|
| `app_escritorio.py` | **Lanzador de la app** (ventana propia + auto-update) |
| `actualizador.py` | Motor de auto-actualización desde GitHub |
| `servidor.py` | Servidor local del panel (sin Flask) |
| `bot.py` | Motor del bot de trading |
| `ui/index.html` | Panel de control |
| `instalar.bat` | Instalador (dependencias + acceso directo) |
| `VERSION` | Número de versión actual |
| `iniciar.bat` | Arranque alternativo en el navegador (sin ventana propia) |

## Arranque alternativo (sin ventana propia)

Si prefieres el modo clásico en el navegador, ejecuta `iniciar.bat` o
`python servidor.py`.
