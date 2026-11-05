"""Corrige las bibliotecas de PROJ repetidas en dist/iik.app (solo macOS, después de PyInstaller).

rasterio y pyproj traen cada uno su propia copia de PROJ con el mismo nombre de archivo
(``libproj.25.<versión>.dylib``) pero compiladas distinto: la de rasterio no exporta las funciones que
usa pyproj. PyInstaller las junta por nombre y deja a pyproj apuntando a la de rasterio, y al importar
pyproj sale «Symbol not found: _proj_context_create». En Linux y Windows no pasa porque las ruedas
renombran sus bibliotecas.

Este script copia la carpeta ``.dylibs`` original de pyproj dentro de la app y hace que las extensiones
de pyproj carguen esas bibliotecas, no las de rasterio. Después hay que volver a firmar la app::

    python instalador/arreglar_mac.py dist/iik.app
    codesign --force --deep --sign - dist/iik.app
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

PAQUETES = ("pyproj",)  # paquetes que deben usar sus propias bibliotecas
CARPETA = "dylibs"  # nombre corto: la ruta nueva debe caber en el encabezado del binario


def dependencias(binario: Path) -> list[str]:
    salida = subprocess.run(["otool", "-L", str(binario)], capture_output=True, text=True, check=True).stdout
    return [ln.strip().split(" (")[0] for ln in salida.splitlines()[1:] if ln.strip()]


def arreglar(app: Path) -> int:
    frameworks = app / "Contents" / "Frameworks"
    cambios = 0
    for paquete in PAQUETES:
        origen = Path(importlib.util.find_spec(paquete).origin).parent / ".dylibs"
        destino_pkg = frameworks / paquete
        if not origen.is_dir() or not destino_pkg.is_dir():
            print(f"{paquete}: sin bibliotecas propias que arreglar")
            continue
        destino = destino_pkg / CARPETA
        shutil.rmtree(destino, ignore_errors=True)
        shutil.copytree(origen, destino, symlinks=True)
        propias = {p.name for p in destino.iterdir()}
        print(f"{paquete}: copiadas {sorted(propias)}")
        for ext in sorted(destino_pkg.rglob("*.so")):
            if ext.is_symlink():
                continue
            for ref in dependencias(ext):
                nombre = ref.rsplit("/", 1)[-1]
                if nombre not in propias:
                    continue
                nueva = "@loader_path/" + os.path.relpath(destino / nombre, ext.parent)
                if ref == nueva:
                    continue
                subprocess.run(["install_name_tool", "-change", ref, nueva, str(ext)], check=True)
                print(f"  {ext.relative_to(frameworks)}: {ref} -> {nueva}")
                cambios += 1
    return cambios


if __name__ == "__main__":
    app = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/iik.app")
    n = arreglar(app)
    print(f"{n} referencias corregidas")
