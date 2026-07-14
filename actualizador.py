# ============================================================
#   actualizador.py — Auto-actualización desde GitHub
#
#   Revisa tu repositorio de GitHub y, si hay una versión más
#   nueva que la instalada, la descarga y la aplica SIN tocar tu
#   carpeta "datos" (config.json, sesion.json, historial).
#
#   Cómo publicas una actualización:
#     1) Editas el código en tu PC.
#     2) Subes el número nuevo en el archivo VERSION (ej: 9.2.0).
#     3) Haces push a la rama `main` de tu repo.
#   La próxima vez que abras la app, se actualiza sola.
#
#   Repo privado: pon un token de GitHub en la variable de entorno
#   BOT_IFC_GH_TOKEN (o en datos/token_github.txt) y funcionará igual.
# ============================================================

import os
import sys
import io
import re
import zipfile
import shutil
import tempfile

try:
    import requests
except Exception:                       # requests puede no estar en un primer arranque
    requests = None

# ── A QUÉ REPOSITORIO APUNTAMOS ──────────────────────────────
OWNER = "jospherramon-dev"
REPO  = "claude"
RAMA  = "main"

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
DATA_DIR     = os.path.join(BASE_DIR, "datos")

# Cosas que el actualizador NUNCA sobreescribe ni borra (tus datos y el .git).
_PRESERVAR = {
    "datos", ".git", ".github", "__pycache__",
    "config.json", "sesion.json", "historial_operaciones.csv",
    "token_github.txt",
}

_TIMEOUT = 8


# ── Versiones ────────────────────────────────────────────────
def _parse(v):
    """'9.10.2' -> (9, 10, 2). Tolera texto suelto."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def version_local():
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _token():
    tok = os.environ.get("BOT_IFC_GH_TOKEN", "").strip()
    if tok:
        return tok
    ruta = os.path.join(DATA_DIR, "token_github.txt")
    try:
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def _headers(raw=False):
    h = {"User-Agent": "BotIFCAuto-Updater"}
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if raw:
        h["Accept"] = "application/vnd.github.raw"
    else:
        h["Accept"] = "application/vnd.github+json"
    return h


def version_remota():
    """Lee el VERSION del repo. Devuelve el string o None si no se pudo."""
    if requests is None:
        return None
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/VERSION?ref={RAMA}"
    try:
        r = requests.get(url, headers=_headers(raw=True), timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    # Plan B: raw.githubusercontent (solo repos públicos)
    try:
        url2 = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{RAMA}/VERSION"
        r = requests.get(url2, timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return None


def hay_actualizacion():
    """Devuelve (hay_update: bool, local: str, remota: str|None)."""
    loc = version_local()
    rem = version_remota()
    if rem is None:
        return (False, loc, None)
    return (_parse(rem) > _parse(loc), loc, rem)


# ── Descarga y aplicación ────────────────────────────────────
def _descargar_zip():
    """Descarga el zipball de la rama y devuelve un ZipFile en memoria."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/zipball/{RAMA}"
    r = requests.get(url, headers=_headers(), timeout=60)
    r.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(r.content))


def descargar_y_aplicar():
    """Descarga la versión nueva y reemplaza los archivos de código.
    NO toca la carpeta datos/. Devuelve (ok: bool, mensaje: str)."""
    if requests is None:
        return (False, "Falta la librería 'requests'. Reinstala las dependencias.")
    try:
        zf = _descargar_zip()
    except Exception as e:
        return (False, f"No pude descargar la actualización: {e}")

    tmp = tempfile.mkdtemp(prefix="botifc_update_")
    try:
        zf.extractall(tmp)
        # El zip de GitHub trae una carpeta raíz tipo "owner-repo-<sha>/".
        entradas = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        if not entradas:
            return (False, "El paquete descargado venía vacío.")
        raiz = os.path.join(tmp, entradas[0])

        # IMPORTANTE: VERSION se copia SIEMPRE al final. Si la copia falla a
        # mitad (disco lleno, antivirus...), VERSION queda con el número viejo
        # y el próximo arranque REINTENTA la actualización, en vez de creer
        # que ya está al día con código a medias.
        nombres = [n for n in os.listdir(raiz) if n not in _PRESERVAR]
        nombres.sort(key=lambda n: n == "VERSION")   # VERSION al final

        copiados = 0
        for nombre in nombres:
            origen  = os.path.join(raiz, nombre)
            destino = os.path.join(BASE_DIR, nombre)
            try:
                if os.path.isdir(origen):
                    shutil.copytree(origen, destino, dirs_exist_ok=True)
                else:
                    shutil.copy2(origen, destino)
                copiados += 1
            except Exception as e:
                return (False, f"Fallo al copiar '{nombre}': {e}")

        _instalar_dependencias()

        return (True, f"Actualización aplicada ({copiados} elementos). Se reiniciará la app.")
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def _instalar_dependencias():
    """Instala las dependencias del requirements.txt recién descargado, por si
    la versión nueva añadió alguna librería. Si falla, no aborta la
    actualización: la app arrancará y el error se verá en consola."""
    req = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req):
        return
    try:
        import subprocess
        # --user --no-cache-dir: mismas opciones que instalar.bat, para no
        # chocar con el error de permisos de pip en Windows.
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req,
             "--user", "--no-cache-dir", "--quiet", "--disable-pip-version-check"],
            timeout=300,
        )
    except Exception:
        pass


def reiniciar_app():
    """Reinicia el proceso para cargar el código nuevo.

    Usamos subprocess.Popen + salida en vez de os.execv: en Windows, execv
    une los argumentos sin comillas y se rompe con rutas con espacios
    (p. ej. C:\\Users\\Jose Ramon\\...)."""
    # Si nos lanzó abrir_app.bat (bucle), salimos con código 10 y el .bat
    # se encarga de reabrir en la MISMA ventana (limpio, sin procesos sueltos
    # que puedan cerrar la consola).
    if os.environ.get("BOT_IFC_LOOP") == "1":
        os._exit(10)
    # Si no, reabrimos nosotros el proceso.
    import subprocess
    try:
        script = os.path.abspath(sys.argv[0]) if sys.argv else ""
        args = [sys.executable] + ([script] + sys.argv[1:] if script else [])
        subprocess.Popen(args, cwd=BASE_DIR)
    except Exception:
        pass   # como último recurso el usuario reabre a mano
    os._exit(0)


def actualizar_si_hace_falta(logger=print):
    """Flujo completo para el arranque: comprueba, aplica y reinicia.
    Devuelve True si aplicó (y por tanto va a reiniciar)."""
    try:
        hay, loc, rem = hay_actualizacion()
    except Exception as e:
        logger(f"[UPDATE] No pude comprobar actualizaciones: {e}")
        return False
    if rem is None:
        logger("[UPDATE] Sin conexión al repo o repo no accesible. Sigo con la versión local.")
        return False
    if not hay:
        logger(f"[UPDATE] Ya estás en la última versión ({loc}).")
        return False

    logger(f"[UPDATE] Nueva versión disponible: {loc} -> {rem}. Descargando...")
    ok, msg = descargar_y_aplicar()
    logger(f"[UPDATE] {msg}")
    if ok:
        reiniciar_app()          # no retorna
        return True
    return False


if __name__ == "__main__":
    # Uso manual desde consola: python actualizador.py
    hay, loc, rem = hay_actualizacion()
    print(f"Versión local: {loc}")
    print(f"Versión remota: {rem}")
    if hay:
        print("Hay actualización. Aplicando...")
        ok, msg = descargar_y_aplicar()
        print(msg)
    else:
        print("No hay actualización pendiente.")
