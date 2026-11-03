"""Descarga y carga de datos meteorológicos.

Todas las funciones devuelven un :class:`MetData`: un DataFrame horario en UTC
con columnas ``ws_<h>`` y ``wd_<h>`` (velocidad m/s y dirección ° de donde
viene el viento) para cada altura ``h`` disponible, más ``t2m`` (°C) y
``ps`` (hPa) cuando existen.

Fuentes (todas con cobertura completa de México):

=====================  ==============  ===============  =====================
Fuente                 Resolución      Alturas viento   Registro
=====================  ==============  ===============  =====================
ERA5 vía Open-Meteo    0.25° (~28 km)  10 m, 100 m      No
NASA POWER (MERRA-2)   0.5x0.625°      10 m, 50 m       No
ERA5 Copernicus CDS    0.25°           10 m, 100 m      Sí (API key gratuita)
Torre de medición      Puntual         la(s) medida(s)  —
=====================  ==============  ===============  =====================
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

CACHE_DIR = Path(os.environ.get("VIENTOMX_CACHE", Path.home() / ".vientomx" / "cache"))
UA = {"User-Agent": "VientoMX-WRA/0.1 (educational wind resource assessment)"}

ProgressCb = Callable[[float, str], None] | None


@dataclass
class MetData:
    df: pd.DataFrame
    lat: float
    lon: float
    source: str
    elevation: float | None = None  # m s.n.m. del punto de referencia
    notes: list[str] = field(default_factory=list)

    @property
    def heights(self) -> list[float]:
        return sorted(float(c[3:]) for c in self.df.columns if c.startswith("ws_"))

    def summary(self) -> pd.DataFrame:
        rows = []
        for h in self.heights:
            ws = self.df[f"ws_{h:g}"]
            rows.append({"Altura [m]": h, "Media [m/s]": ws.mean(), "Datos": ws.notna().sum(),
                         "Inicio": ws.first_valid_index(), "Fin": ws.last_valid_index()})
        return pd.DataFrame(rows)

    def to_csv(self, path):
        self.df.to_csv(path, index_label="time_utc")


class DownloadError(RuntimeError):
    """Error de descarga con un mensaje legible para el usuario."""


def _snippet(text: str, n=300) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:n] + "…") if len(t) > n else t


def _get_json(url: str, params: dict, source: str, timeout=120, retries=3, backoff=4.0, session=None) -> dict:
    """GET que devuelve JSON, con reintentos y un error claro si el servidor
    (o un proxy/firewall de la red) responde algo que no es JSON."""
    get = (session or requests).get
    last = ""
    for attempt in range(retries):
        wait = backoff * (attempt + 1)
        try:
            r = get(url, params=params, headers=UA, timeout=timeout)
        except requests.RequestException as e:
            last = f"no se pudo conectar ({type(e).__name__}: {e})"
            r = None
        if r is not None:
            ctype = r.headers.get("Content-Type", "")
            try:
                js = r.json()
            except ValueError:
                js = None
            if js is not None and r.ok:
                return js
            if js is not None:  # error del API con explicación en JSON (p. ej. Open-Meteo 400/429)
                reason = js.get("reason") or js.get("message") or js.get("messages") or js.get("detail") or js
                last = f"HTTP {r.status_code}: {reason}"
                if r.status_code == 429:  # límite por minuto del servicio gratuito
                    wait = max(wait, 30.0 * (attempt + 1))
                if r.status_code < 500 and r.status_code != 429:
                    break  # parámetros inválidos: reintentar no ayuda
            else:
                body = _snippet(r.text)
                last = (f"HTTP {r.status_code}, se recibió {ctype or 'contenido sin tipo'} en lugar de JSON"
                        + (f": «{body}»" if body else " (respuesta vacía)"))
        if attempt < retries - 1:
            time.sleep(wait)
    full = requests.Request("GET", url, params=params).prepare().url
    raise DownloadError(
        f"{source} no devolvió datos válidos ({last}). "
        "Si el texto anterior es una página de su red (proxy, firewall, antivirus o portal de acceso), "
        "pruebe con otra red o configure el proxy (variables HTTPS_PROXY / REQUESTS_CA_BUNDLE). "
        f"Puede comprobar la conexión abriendo esta dirección en el navegador: {full}"
    )


def _cache_path(key: dict) -> Path:
    h = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    return CACHE_DIR / f"met_{key.get('src','x')}_{h}.csv.gz"


def _from_cache(key):
    p = _cache_path(key)
    if p.exists():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
        meta = json.loads(p.with_suffix(".json").read_text()) if p.with_suffix(".json").exists() else {}
        return df, meta
    return None, None


def _to_cache(key, df, meta):
    p = _cache_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p)
    p.with_suffix(".json").write_text(json.dumps(meta))


# --------------------------------------------------------------------------
# ERA5 vía Open-Meteo (sin registro)
# --------------------------------------------------------------------------
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


def parse_open_meteo(js: dict) -> pd.DataFrame:
    h = js["hourly"]
    idx = pd.to_datetime(h["time"], utc=True)
    out = pd.DataFrame(index=idx)
    for hgt in (10, 100):
        if f"wind_speed_{hgt}m" in h:
            out[f"ws_{hgt}"] = pd.to_numeric(pd.Series(h[f"wind_speed_{hgt}m"], index=idx), errors="coerce")
            out[f"wd_{hgt}"] = pd.to_numeric(pd.Series(h[f"wind_direction_{hgt}m"], index=idx), errors="coerce")
    if "temperature_2m" in h:
        out["t2m"] = pd.to_numeric(pd.Series(h["temperature_2m"], index=idx), errors="coerce")
    if "surface_pressure" in h:
        out["ps"] = pd.to_numeric(pd.Series(h["surface_pressure"], index=idx), errors="coerce")
    units = js.get("hourly_units", {})
    if units.get("wind_speed_100m", "m/s") in ("km/h", "kmh"):
        for c in out.columns:
            if c.startswith("ws_"):
                out[c] = out[c] / 3.6
    return out


def download_open_meteo_era5(lat, lon, start_year, end_year, model="era5", progress: ProgressCb = None,
                             use_cache=True, timeout=120) -> MetData:
    """Serie horaria ERA5 (reanálisis ECMWF) servida por Open-Meteo. Sin registro.

    Licencia de datos: Copernicus (ERA5) / CC-BY 4.0 (Open-Meteo). Cite ambos.
    """
    key = dict(src="openmeteo", lat=round(lat, 4), lon=round(lon, 4), y0=start_year, y1=end_year, model=model)
    if use_cache:
        df, meta = _from_cache(key)
        if df is not None:
            return MetData(df, lat, lon, f"ERA5 (Open-Meteo, {model})", meta.get("elevation"), ["desde caché"])
    frames, elev = [], None
    years = list(range(start_year, end_year + 1))
    for i, y in enumerate(years):
        if progress:
            progress(i / len(years), f"Descargando ERA5 {y}…")
        params = dict(
            latitude=lat, longitude=lon, start_date=f"{y}-01-01", end_date=f"{y}-12-31",
            hourly="wind_speed_10m,wind_speed_100m,wind_direction_10m,wind_direction_100m,temperature_2m,surface_pressure",
            wind_speed_unit="ms", timezone="GMT", models=model,
        )
        js = _get_json(OPEN_METEO_URL, params, f"Open-Meteo (año {y})", timeout=timeout)
        if js.get("error"):
            raise DownloadError(f"Open-Meteo (año {y}): {js.get('reason', 'error sin detalle')}")
        if not js.get("hourly"):
            raise DownloadError(f"Open-Meteo (año {y}) respondió sin serie horaria: {_snippet(json.dumps(js))}")
        elev = js.get("elevation", elev)
        frames.append(parse_open_meteo(js))
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated()].dropna(how="all")
    if progress:
        progress(1.0, "ERA5 descargado")
    if use_cache:
        _to_cache(key, df, {"elevation": elev})
    return MetData(df, lat, lon, f"ERA5 (Open-Meteo, {model})", elev)


# --------------------------------------------------------------------------
# NASA POWER (MERRA-2) (sin registro)
# --------------------------------------------------------------------------
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"


def parse_nasa_power(js: dict) -> tuple[pd.DataFrame, float | None]:
    par = js["properties"]["parameter"]
    idx = None
    out = {}
    mapping = {"WS10M": "ws_10", "WD10M": "wd_10", "WS50M": "ws_50", "WD50M": "wd_50", "T2M": "t2m", "PS": "ps"}
    for k, col in mapping.items():
        if k not in par:
            continue
        s = pd.Series(par[k], dtype=float)
        if idx is None:
            idx = pd.to_datetime(s.index, format="%Y%m%d%H", utc=True)
        s.index = idx
        s = s.where(s > -998)
        out[col] = s
    df = pd.DataFrame(out)
    if "ps" in df:
        df["ps"] = df["ps"] * 10.0  # kPa -> hPa
    elev = None
    try:
        elev = float(js["geometry"]["coordinates"][2])
    except Exception:
        pass
    return df, elev


def download_nasa_power(lat, lon, start_year, end_year, progress: ProgressCb = None, use_cache=True,
                        timeout=180) -> MetData:
    """Serie horaria NASA POWER (basada en MERRA-2). Viento a 10 y 50 m."""
    key = dict(src="nasapower", lat=round(lat, 4), lon=round(lon, 4), y0=start_year, y1=end_year)
    if use_cache:
        df, meta = _from_cache(key)
        if df is not None:
            return MetData(df, lat, lon, "NASA POWER (MERRA-2)", meta.get("elevation"), ["desde caché"])
    frames, elev = [], None
    years = list(range(start_year, end_year + 1))
    for i, y in enumerate(years):
        if progress:
            progress(i / len(years), f"Descargando NASA POWER {y}…")
        params = dict(parameters="WS10M,WD10M,WS50M,WD50M,T2M,PS", community="RE", longitude=lon, latitude=lat,
                      start=f"{y}0101", end=f"{y}1231", format="JSON", **{"time-standard": "UTC"})
        js = _get_json(NASA_POWER_URL, params, f"NASA POWER (año {y})", timeout=timeout)
        if "properties" not in js:
            raise DownloadError(f"NASA POWER (año {y}) respondió sin datos: {_snippet(json.dumps(js))}")
        df_y, elev = parse_nasa_power(js)
        frames.append(df_y)
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated()].dropna(how="all")
    if progress:
        progress(1.0, "NASA POWER descargado")
    if use_cache:
        _to_cache(key, df, {"elevation": elev})
    return MetData(df, lat, lon, "NASA POWER (MERRA-2)", elev)


# --------------------------------------------------------------------------
# ERA5 directo de Copernicus CDS (requiere ~/.cdsapirc con API key)
# --------------------------------------------------------------------------

def download_era5_cds(lat, lon, start_year, end_year, target_dir=None, progress: ProgressCb = None,
                      url: str | None = None, key: str | None = None) -> MetData:
    """Descarga ERA5 desde el Climate Data Store. Requiere ``pip install vientomx[era5]``
    y una cuenta gratuita en https://cds.climate.copernicus.eu (API key en ~/.cdsapirc
    o pasada como argumento)."""
    try:
        import cdsapi
        import xarray as xr
    except ImportError as e:  # pragma: no cover
        raise ImportError("Instale los extras: pip install 'vientomx[era5]'") from e
    target_dir = Path(target_dir or CACHE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client(url=url, key=key) if (url and key) else cdsapi.Client()
    area = [lat + 0.25, lon - 0.25, lat - 0.25, lon + 0.25]
    frames = []
    years = list(range(start_year, end_year + 1))
    for i, y in enumerate(years):
        if progress:
            progress(i / len(years), f"Solicitando ERA5 {y} al CDS (puede tardar)…")
        fn = target_dir / f"era5_{lat:.2f}_{lon:.2f}_{y}.nc"
        if not fn.exists():
            client.retrieve(
                "reanalysis-era5-single-levels",
                {
                    "product_type": ["reanalysis"],
                    "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind",
                                 "100m_u_component_of_wind", "100m_v_component_of_wind",
                                 "2m_temperature", "surface_pressure"],
                    "year": [str(y)], "month": [f"{m:02d}" for m in range(1, 13)],
                    "day": [f"{d:02d}" for d in range(1, 32)], "time": [f"{h:02d}:00" for h in range(24)],
                    "area": area, "data_format": "netcdf", "download_format": "unarchived",
                },
                str(fn),
            )
        ds = xr.open_dataset(fn)
        tname = "valid_time" if "valid_time" in ds.dims else "time"
        p = ds.interp(latitude=lat, longitude=lon)
        d = pd.DataFrame(index=pd.to_datetime(p[tname].values, utc=True))
        for hgt in (10, 100):
            u, v = p[f"u{hgt}"].values, p[f"v{hgt}"].values
            d[f"ws_{hgt}"] = np.hypot(u, v)
            d[f"wd_{hgt}"] = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
        d["t2m"] = p["t2m"].values - 273.15
        d["ps"] = p["sp"].values / 100.0
        frames.append(d)
    df = pd.concat(frames).sort_index()
    return MetData(df, lat, lon, "ERA5 (Copernicus CDS)")


# --------------------------------------------------------------------------
# Datos medidos (torre / LiDAR) en CSV
# --------------------------------------------------------------------------

def load_measured_csv(file, time_col, ws_cols: dict[float, str], wd_col: str | None, wd_height: float | None = None,
                      lat=None, lon=None, tz="UTC", resample="1h", t_col=None, p_col=None, sep=None) -> MetData:
    """Carga datos de una torre de medición o LiDAR.

    ``ws_cols`` mapea altura -> nombre de columna. Los datos se promedian a
    1 hora (vectorialmente para la dirección). ``tz`` es la zona horaria de los
    registros (p. ej. 'America/Mexico_City' o 'Etc/GMT+6').
    """
    raw = pd.read_csv(file, sep=sep, engine="python")
    t = pd.to_datetime(raw[time_col], errors="coerce", dayfirst=False)
    raw = raw.loc[t.notna()].copy()
    t = t[t.notna()]
    if t.dt.tz is None:
        t = t.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    raw.index = t.dt.tz_convert("UTC")
    raw = raw[raw.index.notna()]
    out = pd.DataFrame(index=raw.index)
    for h, c in ws_cols.items():
        out[f"ws_{h:g}"] = pd.to_numeric(raw[c], errors="coerce").where(lambda s: (s >= 0) & (s < 75))
    if wd_col:
        wd = pd.to_numeric(raw[wd_col], errors="coerce")
        href = wd_height or max(ws_cols)
        sref = out[f"ws_{href:g}"]
        out["_u"] = -sref * np.sin(np.radians(wd))
        out["_v"] = -sref * np.cos(np.radians(wd))
    if t_col:
        out["t2m"] = pd.to_numeric(raw[t_col], errors="coerce")
    if p_col:
        out["ps"] = pd.to_numeric(raw[p_col], errors="coerce")
    if resample:
        out = out.resample(resample).mean()
    if wd_col:
        wd_h = (np.degrees(np.arctan2(-out["_u"], -out["_v"])) + 360) % 360
        for h in ws_cols:
            out[f"wd_{h:g}"] = wd_h
        out = out.drop(columns=["_u", "_v"])
    out = out.dropna(how="all")
    return MetData(out, lat, lon, "Medición (torre/LiDAR)")


def measured_csv_columns(file, sep=None) -> list[str]:
    pos = file.tell() if hasattr(file, "tell") else None
    cols = list(pd.read_csv(file, sep=sep, engine="python", nrows=5).columns)
    if pos is not None:
        file.seek(pos)
    return cols


def synthetic_met(lat=16.55, lon=-94.95, years=(2015, 2019), mean100=9.0, seed=0) -> MetData:
    """Serie sintética tipo Istmo de Tehuantepec (vientos del N y del S),
    útil para pruebas y demostraciones sin internet."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31 23:00", freq="1h", tz="UTC")
    n = len(idx)
    doy = idx.dayofyear.to_numpy()
    hour = idx.hour.to_numpy()
    # régimen de "nortes" en invierno, más débil en verano, viento del sur ocasional
    north = rng.random(n) < (0.75 + 0.15 * np.cos(2 * np.pi * (doy - 15) / 365))
    wd = np.where(north, rng.normal(355, 12, n), rng.normal(175, 20, n)) % 360
    # escala Weibull (k ~2.2) estacional + ciclo diurno; ajuste a la media deseada
    seas = 1 + 0.25 * np.cos(2 * np.pi * (doy - 15) / 365)
    diur = 1 + 0.08 * np.cos(2 * np.pi * (hour - 4) / 24)
    ar = np.zeros(n)
    e = rng.normal(0, 1, n)
    for i in range(1, n):
        ar[i] = 0.97 * ar[i - 1] + 0.243 * e[i]
    u = np.exp(0.35 * ar) * seas * diur * np.where(north, 1.0, 0.6)
    ws100 = u * mean100 / u.mean()
    alpha = 0.14 + 0.06 * np.cos(2 * np.pi * (hour - 6) / 24)
    ws10 = ws100 * (10 / 100) ** alpha
    df = pd.DataFrame({"ws_10": ws10, "wd_10": (wd - 5) % 360, "ws_100": ws100, "wd_100": wd,
                       "t2m": 27 + 4 * np.cos(2 * np.pi * (hour - 21) / 24), "ps": 1005.0}, index=idx)
    return MetData(df, lat, lon, "Sintético (demo)", elevation=30.0)
