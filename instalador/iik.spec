# -*- mode: python ; coding: utf-8 -*-
# Empaquetado de iik con PyInstaller (Windows y macOS). Desde la raíz del repositorio:
#
#     pip install -r requirements.txt pyinstaller
#     pyinstaller instalador/iik.spec --noconfirm
#
# Resultado: dist/iik/ (Windows: dist/iik/iik.exe) o dist/iik.app (macOS). En Windows, el instalador
# se arma después con Inno Setup (instalador/iik.iss); en macOS, el .dmg con instalador/hacer_dmg.sh.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

REPO = Path(SPECPATH).parent
INST = Path(SPECPATH)
sys.path.insert(0, str(REPO))
from vientomx import __version__ as VERSION  # noqa: E402

# Archivos de la app que Streamlit lee del disco al correr (no se compilan).
datas = [
    (str(REPO / "app"), "app"),
    (str(REPO / "brand"), "brand"),
    (str(REPO / "docs"), "docs"),
    (str(REPO / ".streamlit" / "config.toml"), ".streamlit"),
]
binaries = []
# streamlit_app.py se analiza como módulo para que PyInstaller encuentre todo lo que importa
# (incluidos los import dentro de funciones: folium, scipy.spatial, pyproj, ...).
hiddenimports = ["streamlit_app", "nube", "tema"] + collect_submodules("vientomx")

# Paquetes con archivos de datos, extensiones compiladas o metadatos que hay que copiar completos.
for paquete in ("streamlit", "streamlit_folium", "rasterio", "turbine_models", "windpowerlib"):
    d, b, h = collect_all(paquete)
    datas += d
    binaries += b
    hiddenimports += h
for paquete in ("pyproj", "folium", "branca", "plotly", "altair", "certifi"):
    datas += collect_data_files(paquete)
# Paquetes que se buscan con importlib.metadata al importarse.
for paquete in ("streamlit", "pandas", "numpy", "pyarrow", "altair", "plotly", "rasterio", "pyproj",
                "requests", "cdsapi", "xarray", "netCDF4", "turbine-models", "windpowerlib"):
    try:
        datas += copy_metadata(paquete)
    except Exception:  # paquete no instalado: se omite
        pass

a = Analysis(
    [str(INST / "lanzador.py")],
    pathex=[str(REPO), str(REPO / "app")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Lo que no usa la app y pesa (pruebas, cuadernos, otros entornos gráficos).
    excludes=["tkinter", "pytest", "IPython", "jupyter", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="iik",
    # Windows: ventana de consola con el aviso «no cierre esta ventana» (cerrarla cierra iik).
    # macOS: sin terminal; la bitácora queda en ~/.vientomx/iik-escritorio.log.
    console=sys.platform == "win32",
    icon=str(INST / ("iik.ico" if sys.platform == "win32" else "iik.icns")),
    upx=False,
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="iik", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="iik.app",
        icon=str(INST / "iik.icns"),
        bundle_identifier="mx.iik.wra",
        version=VERSION,
        info_plist={
            "CFBundleName": "iik",
            "CFBundleDisplayName": "iik",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "15.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "GPL-3.0-or-later",
            # Sin ícono en el Dock: iik vive en el navegador y se cierra con «Cerrar iik» o al cerrar
            # todas sus pestañas.
            "LSUIElement": True,
        },
    )
