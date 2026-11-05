"""Lanzador de iik de escritorio (el programa que abre el instalador de Windows o la app de Mac).

Levanta el servidor de Streamlit en la propia computadora (solo en 127.0.0.1, sin abrir puertos a la
red) y abre la app en el navegador. Se usa empaquetado con PyInstaller (``instalador/iik.spec``), pero
también corre con Python normal para probarlo::

    python instalador/lanzador.py              # abre iik en el navegador
    python instalador/lanzador.py --verificar  # solo revisa que todo lo necesario se pueda importar

Comportamiento:

* Si iik ya está abierto (otro doble clic), no levanta un segundo servidor: solo abre otra pestaña.
* Usa el puerto 8765; si está ocupado por otro programa, toma uno libre.
* Se cierra solo unos minutos después de que se cierran todas las pestañas de iik (en Mac no hay
  ventana que cerrar), o con el botón «Cerrar iik» de la barra lateral.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PUERTO = 8765  # puerto preferido de la app
PUERTO_CANDADO = 8764  # solo para saber si ya hay un iik abierto en esta computadora
# tras cerrar la última pestaña, minutos antes de cerrarse solo (0 = nunca)
MINUTOS_SIN_PESTANAS = float(os.environ.get("IIK_MINUTOS_SIN_PESTANAS", 3))
ESTADO = Path.home() / ".vientomx" / "escritorio.json"  # puerto del iik abierto


def carpeta_base() -> Path:
    """Carpeta con ``app/``, ``vientomx/``, ``brand/`` y ``docs/`` (dentro del paquete o en el repo)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def _bitacora() -> None:
    """Sin consola (app de Mac) no hay dónde escribir: los mensajes van a ~/.vientomx/iik-escritorio.log."""
    sin_consola = sys.stdout is None or sys.stderr is None
    app_mac = getattr(sys, "frozen", False) and sys.platform == "darwin"
    if not (sin_consola or app_mac):
        return  # en Windows (y al correrlo desde una terminal) la consola es la bitácora
    try:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        f = open(ESTADO.parent / "iik-escritorio.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = f
    except OSError:
        devnull = open(os.devnull, "w")
        sys.stdout = sys.stderr = devnull


def _certificados() -> None:
    """GDAL (rasterio, /vsicurl) y requests usan los certificados de certifi incluidos en el paquete."""
    try:
        import certifi

        os.environ.setdefault("CURL_CA_BUNDLE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass


def responde(puerto: int, espera: float = 1.0) -> bool:
    """True si hay un servidor de Streamlit sano en ``puerto``."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/_stcore/health", timeout=espera) as r:
            return r.status == 200
    except Exception:
        return False


def puerto_libre(preferido: int = PUERTO) -> int:
    for p in (preferido, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if sys.platform != "win32":  # igual que el servidor: ignora conexiones viejas en TIME_WAIT
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No hay puertos libres")


def tomar_candado() -> socket.socket | None:
    """Ocupa un puerto fijo mientras iik esté abierto. None si otro iik ya lo tiene."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":  # que otro proceso no pueda compartir el puerto
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
    try:
        s.bind(("127.0.0.1", PUERTO_CANDADO))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def abrir_navegador(puerto: int) -> None:
    webbrowser.open(f"http://127.0.0.1:{puerto}", new=2)


def ya_abierto() -> bool:
    """Si otro iik está abierto (o arrancando), abre su página y devuelve True."""
    print("iik ya está abierto en esta computadora: abriendo otra pestaña…")
    limite = time.time() + 120  # la primera vez en Mac puede tardar en arrancar
    while time.time() < limite:
        try:
            puerto = int(json.loads(ESTADO.read_text())["puerto"])
        except Exception:
            puerto = PUERTO
        if responde(puerto):
            abrir_navegador(puerto)
            return True
        time.sleep(1)
    return False


def _vigilar(puerto: int, abrir: bool) -> None:
    """Abre el navegador cuando el servidor responde y cierra iik si ya nadie lo usa."""
    limite = time.time() + 180
    while not responde(puerto, 2) and time.time() < limite:
        time.sleep(0.3)
    print(f"\n  iik está abierto en  http://127.0.0.1:{puerto}\n")
    if sys.platform == "win32":
        print("  No cierre esta ventana mientras use iik. Ciérrela para salir.\n")
    if abrir:
        abrir_navegador(puerto)
    if MINUTOS_SIN_PESTANAS <= 0:
        return
    try:
        from streamlit.runtime import Runtime
    except ImportError:
        return
    hubo, sin_desde, inicio = False, None, time.time()
    while True:
        time.sleep(2)
        try:
            n = Runtime.instance()._session_mgr.num_active_sessions()
        except Exception:  # API interna de Streamlit: si cambia, simplemente no se cierra solo
            return
        if n > 0:
            hubo, sin_desde = True, None
        elif not hubo and time.time() - inicio > 10 * 60:
            print("Nadie abrió iik en 10 minutos: cerrando.")
            os._exit(0)
        elif hubo:
            sin_desde = sin_desde or time.time()
            if time.time() - sin_desde > MINUTOS_SIN_PESTANAS * 60:
                print("No quedan pestañas de iik abiertas: cerrando.")
                os._exit(0)


def verificar() -> int:
    """Importa todo lo que usa la app (para revisar el paquete sin abrir el navegador)."""
    base = carpeta_base()
    sys.path[:0] = [str(base / "app"), str(base)]
    import importlib

    mods = [
        "streamlit", "plotly", "folium", "streamlit_folium", "rasterio", "rasterio.warp", "pyproj",
        "h5py", "scipy.spatial", "matplotlib.pyplot", "reportlab", "openpyxl", "turbine_models",
        "windpowerlib", "cdsapi", "xarray", "netCDF4", "vientomx.aep", "vientomx.pdf_report",
        "vientomx.terrain", "vientomx.wtk", "vientomx.osm",
    ]
    fallas = 0
    for m in mods:
        try:
            importlib.import_module(m)
            print(f"ok     {m}")
        except Exception as e:  # noqa: BLE001
            fallas += 1
            print(f"FALLA  {m}: {e!r}")
    try:
        from pyproj import Transformer

        x, _ = Transformer.from_crs(4326, 32615, always_xy=True).transform(-94.95, 16.55)
        print(f"ok     pyproj (proj.db)  x={x:.0f}")
        from rasterio.crs import CRS

        print(f"ok     rasterio (gdal_data)  {CRS.from_epsg(32614).to_string()}")
        from vientomx import turbines

        print(f"ok     librería de turbinas: {len(turbines.load_library())} modelos")
        for f in ("app/streamlit_app.py", "app/tema.py", "app/nube.py", "docs/metodo.md", "brand/iik-icon-dark.svg"):
            assert (base / f).exists(), f"falta {f}"
        print("ok     archivos de la app")
    except Exception as e:  # noqa: BLE001
        fallas += 1
        print(f"FALLA  {e!r}")
    print("Todo listo." if not fallas else f"{fallas} fallas.")
    return 1 if fallas else 0


def main() -> int:
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    for flujo in (sys.stdout, sys.stderr):  # que un acento no tumbe la consola de Windows
        try:
            flujo.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    _certificados()
    if "--verificar" in args:
        return verificar()
    _bitacora()
    abrir = "--sin-navegador" not in args and not os.environ.get("IIK_SIN_NAVEGADOR")

    candado = tomar_candado()
    if candado is None and ya_abierto():
        return 0

    base = carpeta_base()
    puerto = int(os.environ.get("IIK_PUERTO") or puerto_libre())
    try:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps({"puerto": puerto, "pid": os.getpid()}))
    except OSError:
        pass
    os.environ["IIK_ESCRITORIO"] = "1"  # la app muestra el botón «Cerrar iik»
    os.environ.setdefault("IIK_SERVIDOR_COMPARTIDO", "0")  # un solo usuario: sin límites de servidor
    os.chdir(base)  # para que Streamlit lea .streamlit/config.toml

    print("Abriendo iik… (la primera vez puede tardar un poco)")
    threading.Thread(target=_vigilar, args=(puerto, abrir), daemon=True).start()

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(base / "app" / "streamlit_app.py"),
        "--global.developmentMode=false",  # empaquetado, Streamlit creería que es una copia de desarrollo
        "--server.headless=true",  # el navegador lo abre _vigilar cuando el servidor ya responde
        "--server.address=127.0.0.1",  # solo esta computadora: sin aviso del firewall
        f"--server.port={puerto}",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=viewer",
        "--server.maxUploadSize=100",
    ]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
