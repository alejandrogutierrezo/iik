"""Aerogeneradores ya mapeados en OpenStreetMap (vía Overpass API).

En OSM un aerogenerador es un nodo con ``power=generator`` y
``generator:source=wind`` (el icono de turbina que se ve en el mapa). Para
parques ya construidos esto permite tomar el layout directamente del mapa.

Datos © colaboradores de OpenStreetMap, licencia ODbL.
"""
from __future__ import annotations

import math
import os
import re

import pandas as pd
import requests

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# Servidor Overpass propio (opcional), p. ej. para redes institucionales con proxy
if os.environ.get("VIENTOMX_OVERPASS_URL"):
    OVERPASS_URLS = (os.environ["VIENTOMX_OVERPASS_URL"],) + OVERPASS_URLS
MAX_BBOX_DEG2 = 1.0  # ~110 x 110 km; Overpass rechaza o tarda mucho con áreas mayores
USER_AGENT = "VientoMX/0.1 (wind resource assessment; https://github.com/alejandrogutierrezo/iik)"
COLUMNS = ["osm_id", "lat", "lon", "nombre", "ref", "fabricante", "modelo", "potencia_kw", "altura_buje_m",
           "diametro_rotor_m", "operador"]


def build_query(south: float, west: float, north: float, east: float, timeout: int = 60) -> str:
    bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    return (f"[out:json][timeout:{timeout}];"
            f'(nwr["power"="generator"]["generator:source"="wind"]({bbox}););'
            "out center tags;")


def _num(v, unit_kw=False):
    """'2.3 MW' -> 2300 (kW) si unit_kw; '95 m' -> 95.0."""
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)", s)
    if not m:
        return None
    x, u = float(m.group(1)), m.group(2).lower()
    if unit_kw:
        return x * {"w": 1e-3, "kw": 1.0, "mw": 1e3, "gw": 1e6}.get(u, 1.0 if x > 50 else 1e3)
    if u in ("ft", "feet"):
        return x * 0.3048
    return x


def parse_overpass(data: dict) -> pd.DataFrame:
    """Convierte la respuesta JSON de Overpass en un DataFrame de turbinas."""
    rows = []
    for el in data.get("elements", []):
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        t = el.get("tags", {})
        rows.append({
            "osm_id": f"{el.get('type', 'node')[0]}{el.get('id')}",
            "lat": float(lat), "lon": float(lon),
            "nombre": t.get("name"),
            "ref": t.get("ref"),
            "fabricante": t.get("manufacturer"),
            "modelo": t.get("model"),
            "potencia_kw": _num(t.get("generator:output:electricity"), unit_kw=True),
            "altura_buje_m": _num(t.get("height:hub") or t.get("hub_height")),
            "diametro_rotor_m": _num(t.get("rotor:diameter")),
            "operador": t.get("operator"),
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = df.drop_duplicates("osm_id").sort_values(["lat", "lon"], ascending=[False, True]).reset_index(drop=True)
    return df


def fetch_wind_turbines(south: float, west: float, north: float, east: float,
                        urls=OVERPASS_URLS, timeout: int = 60) -> pd.DataFrame:
    """Descarga los aerogeneradores de OSM dentro del rectángulo (grados decimales)."""
    if not (south < north and west < east):
        raise ValueError("Rectángulo inválido: se requiere sur < norte y oeste < este.")
    if (north - south) * (east - west) > MAX_BBOX_DEG2:
        raise ValueError("El área es demasiado grande para consultar OSM; acerque el mapa al parque.")
    q = build_query(south, west, north, east, timeout)
    errors = []
    for url in urls:
        try:
            r = requests.post(url, data={"data": q}, timeout=timeout + 15, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return parse_overpass(r.json())
        except Exception as e:  # probar el siguiente servidor espejo
            errors.append(f"{url.split('/')[2]}: {e}")
    raise RuntimeError("No se pudo consultar Overpass/OpenStreetMap. " + " | ".join(errors))


def bbox_around(lat: float, lon: float, radius_km: float):
    """(sur, oeste, norte, este) de un cuadrado de ±radius_km alrededor del punto."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def to_layout(df: pd.DataFrame, prefix: str = "WTG") -> pd.DataFrame:
    """Convierte turbinas OSM al formato de layout de VientoMX (``id, lat, lon`` [+ ``hub_height``])."""
    ids = []
    for i, r in enumerate(df.itertuples(index=False)):
        lab = r.ref or r.nombre
        ids.append(str(lab) if lab and str(lab) not in ids else f"{prefix}{i+1:02d}")
    out = pd.DataFrame({"id": ids, "lat": df.lat.to_numpy(), "lon": df.lon.to_numpy()})
    if "altura_buje_m" in df and df.altura_buje_m.notna().any():
        out["hub_height"] = pd.to_numeric(df.altura_buje_m, errors="coerce").to_numpy()
    out["osm_id"] = df.osm_id.to_numpy()
    return out
