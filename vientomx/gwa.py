"""Velocidad media del Global Wind Atlas (GWA, DTU/Banco Mundial) en puntos.

El GWA da la velocidad media de largo plazo a 10, 50, 100, 150 y 200 m con
resolución de 250 m (mesoescala WRF + reducción de escala de microescala con
el modelo lineal de WAsP). Se usa para **calibrar** la media de una serie de
reanálisis (que conserva su forma: distribución, rosa, ciclos).

Dos vías:

* ``fetch_mean_speed``: lee la ventana necesaria del GeoTIFF nacional (México)
  directamente del servidor del GWA (lectura parcial con GDAL ``/vsicurl``) y,
  si el servidor no la permite, descarga el archivo completo una vez a la caché.
* ``sample_raster``: el usuario descarga un GeoTIFF de velocidad media desde
  https://globalwindatlas.info (Download → GIS files, o un área dibujada) y lo sube.

Licencia de datos: CC BY 4.0. Cite "Global Wind Atlas 3/4, DTU Wind Energy".
El GWA sigue siendo un modelo: en terreno complejo puede errar ±10–20 % en velocidad.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import requests

from .met import CACHE_DIR, UA, DownloadError, ProgressCb

HEIGHTS = (10, 50, 100, 150, 200)
URL_TEMPLATE = os.environ.get("VIENTOMX_GWA_URL",
                              "https://globalwindatlas.info/api/gis/country/{iso3}/wind-speed/{height}")
HEADERS = {**UA, "Referer": "https://globalwindatlas.info/"}


def nearest_height(h: float) -> int:
    return int(min(HEIGHTS, key=lambda z: abs(np.log(z / h))))


def sample_raster(src, lats, lons) -> np.ndarray:
    """Valor del ráster en cada (lat, lon) (vecino más cercano). ``src``: ruta, URL GDAL o archivo subido."""
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform

    lats, lons = np.atleast_1d(lats).astype(float), np.atleast_1d(lons).astype(float)

    def _do(ds):
        xs, ys = (lons, lats) if ds.crs is None or ds.crs.to_epsg() == 4326 else transform("EPSG:4326", ds.crs, lons, lats)
        vals = np.array([v[0] for v in ds.sample(list(zip(xs, ys)))], dtype=float)
        if ds.nodata is not None:
            vals[vals == ds.nodata] = np.nan
        vals[(vals < 0) | (vals > 60)] = np.nan
        return vals

    if hasattr(src, "read") or isinstance(src, (bytes, bytearray)):
        data = src.read() if hasattr(src, "read") else src
        with MemoryFile(data) as mf, mf.open() as ds:
            return _do(ds)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_HEADERS="Referer: https://globalwindatlas.info/",
                      GDAL_HTTP_USERAGENT=UA["User-Agent"], GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2",
                      CPL_VSIL_CURL_USE_HEAD="NO"):
        with rasterio.open(src) as ds:
            return _do(ds)


def _cache_file(iso3: str, height: int) -> Path:
    return CACHE_DIR / f"gwa_{iso3.lower()}_ws{height}.tif"


def download_country_raster(height: int, iso3: str = "MEX", progress: ProgressCb = None, timeout=300) -> Path:
    """Descarga completa (una sola vez) del GeoTIFF nacional del GWA a la caché."""
    p = _cache_file(iso3, height)
    if p.exists() and p.stat().st_size > 1000:
        return p
    url = URL_TEMPLATE.format(iso3=iso3, height=height)
    try:
        r = requests.get(url, headers=HEADERS, stream=True, timeout=timeout)
    except requests.RequestException as e:
        raise DownloadError(f"Global Wind Atlas: no se pudo conectar ({e}).") from e
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200 or "html" in ctype or "json" in ctype:
        body = r.text[:300] if r.status_code != 200 or "html" in ctype or "json" in ctype else ""
        raise DownloadError(f"Global Wind Atlas respondió HTTP {r.status_code} ({ctype}) en {url}: {body}")
    total = int(r.headers.get("Content-Length", 0)) or None
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    done = 0
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(min(done / total, 0.99) if total else 0.5,
                         f"Descargando GWA {height} m: {done / 1e6:.0f}" + (f" de {total / 1e6:.0f} MB" if total else " MB"))
    tmp.replace(p)
    return p


def fetch_mean_speed(lats, lons, height: int = 100, iso3: str = "MEX", progress: ProgressCb = None) -> np.ndarray:
    """Velocidad media GWA [m/s] a ``height`` m en los puntos dados."""
    height = nearest_height(height)
    cached = _cache_file(iso3, height)
    if cached.exists():
        return sample_raster(str(cached), lats, lons)
    url = URL_TEMPLATE.format(iso3=iso3, height=height)
    errors = []
    try:  # 1) lectura parcial remota (solo los bloques necesarios)
        if progress:
            progress(0.1, f"Leyendo Global Wind Atlas {height} m…")
        vals = sample_raster(f"/vsicurl/{url}", lats, lons)
        if np.isfinite(vals).any():
            return vals
        errors.append("la lectura parcial no devolvió valores")
    except Exception as e:
        errors.append(f"lectura parcial: {e}")
    try:  # 2) descarga completa a la caché
        p = download_country_raster(height, iso3, progress)
        return sample_raster(str(p), lats, lons)
    except Exception as e:
        errors.append(str(e))
    raise DownloadError(
        "No se pudo obtener el Global Wind Atlas automáticamente (" + "; ".join(errors) + "). "
        "Alternativa: en https://globalwindatlas.info elija el área, descargue el GeoTIFF de "
        f"'Mean wind speed' a {height} m y súbalo aquí, o escriba el valor a mano.")
