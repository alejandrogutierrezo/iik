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
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import pandas as pd
import requests

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
# Servidor Overpass propio (opcional), p. ej. para redes institucionales con proxy
if os.environ.get("VIENTOMX_OVERPASS_URL"):
    OVERPASS_URLS = (os.environ["VIENTOMX_OVERPASS_URL"],) + OVERPASS_URLS
MAX_BBOX_DEG2 = 1.0  # ~110 x 110 km; Overpass rechaza o tarda mucho con áreas mayores
DEADLINE_S = 40  # tiempo máximo total de la búsqueda (todos los servidores en paralelo)
USER_AGENT = "VientoMX/0.1 (wind resource assessment; https://github.com/alejandrogutierrezo/iik)"
COLUMNS = ["osm_id", "lat", "lon", "nombre", "ref", "fabricante", "modelo", "potencia_kw", "altura_buje_m",
           "diametro_rotor_m", "operador"]


def build_query(south: float, west: float, north: float, east: float, timeout: int = 25) -> str:
    """Consulta Overpass: aerogeneradores (con cualquiera de las dos etiquetas usadas en OSM) y
    polígonos de parques eólicos (``power=plant`` + ``plant:source=wind``)."""
    bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    return (f"[out:json][timeout:{timeout}][bbox:{bbox}];"
            '(nwr["power"="generator"]["generator:source"="wind"];'
            'nwr["power"="generator"]["generator:method"="wind_turbine"];'
            'nwr["power"="plant"]["plant:source"="wind"];);'
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
        if t.get("power") == "plant":  # polígono del parque, no una turbina
            continue
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


def parse_plants(data: dict) -> pd.DataFrame:
    """Parques eólicos (``power=plant``) de la respuesta de Overpass: nombre, centro y capacidad."""
    rows = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        if t.get("power") != "plant":
            continue
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None or c.get("lon") is None:
            continue
        rows.append({"osm_id": f"{el.get('type', 'way')[0]}{el.get('id')}", "nombre": t.get("name"),
                     "lat": float(c["lat"]), "lon": float(c["lon"]), "operador": t.get("operator"),
                     "potencia_kw": _num(t.get("plant:output:electricity"), unit_kw=True)})
    return pd.DataFrame(rows, columns=["osm_id", "nombre", "lat", "lon", "operador", "potencia_kw"])


def _query_one(url: str, q: str, read_timeout: float) -> dict:
    r = requests.post(url, data={"data": q}, timeout=(8, read_timeout),
                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:  # Overpass devuelve HTML/XML cuando está saturado o la consulta caducó
        raise RuntimeError(f"respuesta no JSON (HTTP {r.status_code})") from None
    if "elements" not in data:
        raise RuntimeError("respuesta sin 'elements'")
    rem = str(data.get("remark", ""))
    if "runtime error" in rem.lower() or "timed out" in rem.lower():
        raise RuntimeError(rem[:200])
    return data


def query_overpass(q: str, urls=None, deadline: float = DEADLINE_S) -> dict:
    """Envía la consulta a todos los servidores espejo **en paralelo** y devuelve la primera
    respuesta válida. Nunca tarda más de ``deadline`` segundos (antes podía quedarse varios
    minutos "cargando" esperando a un servidor saturado)."""
    urls = tuple(urls or OVERPASS_URLS)
    errors: list[str] = []
    t0 = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=max(len(urls), 1))
    futs = {ex.submit(_query_one, u, q, deadline): u for u in urls}
    try:
        pending = set(futs)
        while pending:
            left = deadline - (time.monotonic() - t0)
            if left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    return f.result()
                except Exception as e:  # probar con la siguiente respuesta
                    errors.append(f"{futs[f].split('/')[2]}: {e}")
        for f in pending:
            errors.append(f"{futs[f].split('/')[2]}: sin respuesta en {deadline:.0f} s")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("No se pudo consultar Overpass/OpenStreetMap (servidores saturados o bloqueados por la red). "
                       "Intente de nuevo en un minuto, acerque más el mapa, o importe un GeoJSON exportado de "
                       "overpass-turbo.eu. Detalle: " + " | ".join(errors))


def fetch_osm(south: float, west: float, north: float, east: float, urls=None,
              deadline: float = DEADLINE_S) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(turbinas, parques) de OSM dentro del rectángulo (grados decimales)."""
    if not (south < north and west < east):
        raise ValueError("Rectángulo inválido: se requiere sur < norte y oeste < este.")
    if (north - south) * (east - west) > MAX_BBOX_DEG2:
        raise ValueError("El área es demasiado grande para consultar OSM; acerque el mapa al parque.")
    data = query_overpass(build_query(south, west, north, east, timeout=int(max(deadline - 5, 10))), urls, deadline)
    return parse_overpass(data), parse_plants(data)


def fetch_wind_turbines(south: float, west: float, north: float, east: float,
                        urls=None, deadline: float = DEADLINE_S) -> pd.DataFrame:
    """Descarga los aerogeneradores de OSM dentro del rectángulo (grados decimales)."""
    return fetch_osm(south, west, north, east, urls, deadline)[0]


def parse_geojson(data: dict) -> pd.DataFrame:
    """Turbinas de un GeoJSON exportado de overpass-turbo.eu (o cualquier GeoJSON de puntos).
    Alternativa cuando la red bloquea la API de Overpass."""
    els = []
    for i, f in enumerate(data.get("features", [])):
        g, props = f.get("geometry") or {}, dict(f.get("properties") or {})
        tags = props.pop("tags", None) or props
        coords = g.get("coordinates")
        if g.get("type") == "Point" and coords:
            lon, lat = coords[:2]
        elif g.get("type") in ("Polygon", "LineString", "MultiPolygon") and coords:
            flat = pd.DataFrame(_flatten(coords), columns=["lon", "lat"])
            lon, lat = flat.lon.mean(), flat.lat.mean()
        else:
            continue
        if tags.get("power") == "plant":
            continue
        fid = str(f.get("id") or props.get("@id") or props.get("id") or f"g{i}")
        typ, _, num = fid.partition("/")
        els.append({"type": typ if num else "node", "id": num or fid, "lat": float(lat), "lon": float(lon),
                    "center": {"lat": float(lat), "lon": float(lon)}, "tags": tags})
    return parse_overpass({"elements": els})


def _flatten(c):
    if c and isinstance(c[0], (int, float)):
        return [c[:2]]
    return [p for sub in c for p in _flatten(sub)]


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
