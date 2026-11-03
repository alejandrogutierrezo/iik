"""Carga de coordenadas de aerogeneradores (CSV, Excel, KML/KMZ) y layouts de ejemplo."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer

LAT_KEYS = ("lat", "latitud", "latitude", "y_geo")
LON_KEYS = ("lon", "long", "longitud", "longitude", "lng", "x_geo")
X_KEYS = ("x", "este", "easting", "east", "utm_x", "x_utm", "e")
Y_KEYS = ("y", "norte", "northing", "north", "utm_y", "y_utm", "n")
ID_KEYS = ("id", "nombre", "name", "turbina", "wtg", "aerogenerador")
HH_KEYS = ("hub", "buje", "altura", "hub_height", "hh")


def _find(cols, keys):
    norm = {re.sub(r"[^a-z_]", "", c.lower().strip()): c for c in cols}
    for k in keys:
        if k in norm:
            return norm[k]
    for k in keys:
        if len(k) > 2:
            for n, c in norm.items():
                if n.startswith(k):
                    return c
    return None


def _from_table(df: pd.DataFrame, utm_zone: int | None = None, south=False, epsg: int | None = None) -> pd.DataFrame:
    df = df.dropna(how="all")
    cols = list(df.columns)
    c_id = _find(cols, ID_KEYS)
    c_lat, c_lon = _find(cols, LAT_KEYS), _find(cols, LON_KEYS)
    c_hh = _find(cols, HH_KEYS)
    if c_lat and c_lon:
        lat = pd.to_numeric(df[c_lat], errors="coerce").to_numpy()
        lon = pd.to_numeric(df[c_lon], errors="coerce").to_numpy()
    else:
        c_x, c_y = _find(cols, X_KEYS), _find(cols, Y_KEYS)
        if not (c_x and c_y):
            raise ValueError("No se encontraron columnas de coordenadas. Use lat/lon (grados decimales) "
                             "o x/y (UTM, indicando la zona).")
        x = pd.to_numeric(df[c_x], errors="coerce").to_numpy()
        y = pd.to_numeric(df[c_y], errors="coerce").to_numpy()
        if np.nanmax(np.abs(x)) <= 180 and np.nanmax(np.abs(y)) <= 90:
            lon, lat = x, y
        else:
            if epsg is None:
                if utm_zone is None:
                    raise ValueError("Coordenadas proyectadas: indique la zona UTM (México: 11 a 16).")
                epsg = (32700 if south else 32600) + int(utm_zone)
            tr = Transformer.from_crs(CRS.from_epsg(epsg), 4326, always_xy=True)
            lon, lat = tr.transform(x, y)
    out = pd.DataFrame({
        "id": df[c_id].astype(str).to_numpy() if c_id else [f"WTG{i+1:02d}" for i in range(len(df))],
        "lat": lat, "lon": lon,
    })
    if c_hh:
        out["hub_height"] = pd.to_numeric(df[c_hh], errors="coerce").to_numpy()
    out = out.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    _validate(out)
    return out


def _validate(df):
    if df.empty:
        raise ValueError("El layout no tiene turbinas válidas.")
    if (df.lat.abs() > 90).any() or (df.lon.abs() > 180).any():
        raise ValueError("Coordenadas fuera de rango.")
    if not ((df.lat.between(14, 33.5)) & (df.lon.between(-118.5, -86))).all():
        df.attrs["warning"] = "Algunas turbinas están fuera del territorio mexicano; verifique signos (W = negativo)."


def _parse_kml(text: bytes) -> pd.DataFrame:
    root = ET.fromstring(text)
    ns = {"k": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    q = (lambda t: f"k:{t}") if ns else (lambda t: t)
    rows = []
    for pm in root.iter(f"{{{ns['k']}}}Placemark" if ns else "Placemark"):
        pt = pm.find(f".//{q('Point')}/{q('coordinates')}", ns)
        if pt is None or not pt.text:
            continue
        lon, lat, *_ = [float(v) for v in pt.text.strip().split(",")]
        name = pm.find(q("name"), ns)
        rows.append({"id": name.text.strip() if name is not None and name.text else f"WTG{len(rows)+1:02d}",
                     "lat": lat, "lon": lon})
    if not rows:
        raise ValueError("El KML no contiene puntos (Placemark/Point).")
    df = pd.DataFrame(rows)
    _validate(df)
    return df


def load_layout(file, filename: str | None = None, utm_zone: int | None = None, south=False,
                epsg: int | None = None) -> pd.DataFrame:
    """Lee un layout. Devuelve DataFrame con ``id, lat, lon`` (+ ``hub_height``).

    Formatos: CSV/TXT (coma, punto y coma o tabulador), XLSX/XLS, KML, KMZ
    (Google Earth). Columnas reconocidas (mayúsculas indistintas):
    lat/latitud, lon/longitud, o x/este + y/norte en UTM.
    """
    name = (filename or getattr(file, "name", "") or "").lower()
    data = file.read() if hasattr(file, "read") else open(file, "rb").read()
    if name.endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml = [n for n in z.namelist() if n.lower().endswith(".kml")][0]
            return _parse_kml(z.read(kml))
    if name.endswith(".kml"):
        return _parse_kml(data)
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return _from_table(pd.read_excel(io.BytesIO(data)), utm_zone, south, epsg)
    txt = data.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(txt), sep=None, engine="python")
    return _from_table(df, utm_zone, south, epsg)


def grid_layout(lat_c, lon_c, rows, cols, spacing_row_D, spacing_col_D, rotor_d, orientation_deg=0.0,
                prefix="WTG") -> pd.DataFrame:
    """Layout rectangular centrado en (lat_c, lon_c). Las filas son
    perpendiculares a ``orientation_deg`` (dirección dominante del viento)."""
    from .terrain import utm_crs

    crs = utm_crs(lat_c, lon_c)
    fwd = Transformer.from_crs(4326, crs, always_xy=True)
    inv = Transformer.from_crs(crs, 4326, always_xy=True)
    xc, yc = fwd.transform(lon_c, lat_c)
    th = np.radians(orientation_deg)
    # eje a lo largo de la fila (perpendicular al viento) y eje entre filas (paralelo al viento)
    ax_row = np.array([np.cos(th), -np.sin(th)])
    ax_wind = np.array([np.sin(th), np.cos(th)])
    pts = []
    for r in range(rows):
        for c in range(cols):
            a = (c - (cols - 1) / 2) * spacing_col_D * rotor_d
            b = (r - (rows - 1) / 2) * spacing_row_D * rotor_d
            pts.append((xc + a * ax_row[0] + b * ax_wind[0], yc + a * ax_row[1] + b * ax_wind[1]))
    pts = np.array(pts)
    lon, lat = inv.transform(pts[:, 0], pts[:, 1])
    return pd.DataFrame({"id": [f"{prefix}{i+1:02d}" for i in range(len(pts))], "lat": lat, "lon": lon})


def distance_m(lat1, lon1, lat2, lon2):
    """Distancia de gran círculo [m] (haversine); acepta arreglos."""
    R = 6371008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def nearest(df: pd.DataFrame | None, lat: float, lon: float):
    """(índice, distancia_m) de la turbina más cercana, o (None, inf) si no hay."""
    if df is None or df.empty:
        return None, float("inf")
    d = distance_m(lat, lon, df.lat.to_numpy(float), df.lon.to_numpy(float))
    i = int(np.argmin(d))
    return df.index[i], float(d[i])


def next_id(df: pd.DataFrame | None, prefix="WTG") -> str:
    used = set() if df is None else set(df["id"].astype(str))
    n = len(used) + 1
    while f"{prefix}{n:02d}" in used:
        n += 1
    return f"{prefix}{n:02d}"


def add_turbine(df: pd.DataFrame | None, lat: float, lon: float, tid: str | None = None, **extra) -> pd.DataFrame:
    """Agrega una turbina al layout (crea el layout si ``df`` es None)."""
    if tid is None or (df is not None and tid in set(df["id"].astype(str))):
        tid = next_id(df)
    row = pd.DataFrame([{"id": tid, "lat": float(lat), "lon": float(lon), **extra}])
    if df is None or df.empty:
        return row
    return pd.concat([df, row], ignore_index=True)


def remove_turbine(df: pd.DataFrame, idx) -> pd.DataFrame:
    return df.drop(index=idx).reset_index(drop=True)


def move_turbine(df: pd.DataFrame, idx, lat: float, lon: float) -> pd.DataFrame:
    df = df.copy()
    df.loc[idx, ["lat", "lon"]] = [float(lat), float(lon)]
    if "osm_id" in df:  # ya no coincide con la posición mapeada en OSM
        df.loc[idx, "osm_id"] = None
    return df


# Regiones eólicas de México para ejemplos
MEXICO_SITES = {
    "La Ventosa, Oaxaca (Istmo de Tehuantepec)": (16.55, -94.95),
    "Juchitán, Oaxaca": (16.47, -95.05),
    "Reynosa, Tamaulipas": (25.95, -98.35),
    "General Bravo / Cerralvo, Nuevo León": (26.05, -99.20),
    "La Rumorosa, Baja California": (32.50, -116.05),
    "Villa de Cos, Zacatecas (altiplano)": (23.30, -102.40),
    "Dzilam de Bravo, Yucatán": (21.35, -88.85),
    "Santiago Eólico, Guanajuato/SLP": (22.10, -100.90),
    "Puebla - Esperanza": (18.85, -97.40),
    "Santa Catarina, Nuevo León (autopista Saltillo–Monterrey, aprox.)": (25.60, -100.70),
}
