"""Guardar y cargar la configuración de cada paso del estudio (y del proyecto completo).

Formatos:

- Paso 1 · layout ............ JSON  (``*.vmx-layout.json``)  — tabla de turbinas + vista del mapa.
- Paso 2 · viento ............ ZIP   (``*.vmx-viento.zip``)   — ``config.json`` + series horarias en CSV
  (``serie.csv`` y, si hay MCP, ``reanalisis.csv``). El CSV se puede abrir en Excel.
- Paso 3 · terreno ........... ZIP   (``*.vmx-terreno.zip``)  — ``config.json`` + ``malla.npz`` (elevación,
  rugosidad y cobertura en la malla UTM de cálculo), para no volver a descargar el DEM.
- Paso 4 · aerogenerador ..... JSON  (``*.vmx-turbina.json``) — curva de potencia y Ct completas.
- Paso 5 · simulación ........ JSON  (``*.vmx-simulacion.json``) — estelas, flujo, pérdidas e incertidumbres.
- Proyecto completo .......... ZIP   (``*.vmx-proyecto.zip``) — un archivo por paso de los anteriores.

Todos los archivos llevan una cabecera ``{"formato": "vientomx", "tipo": ..., "version": ...}`` para
reconocer el paso al que pertenecen y avisar si se sube el archivo de otro paso.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import __version__
from .met import MetData
from .terrain import Grid
from .turbines import Turbine

FORMAT = "vientomx"
VERSION = 1

KIND_NAMES = {
    "layout": "1 · Sitio y layout",
    "viento": "2 · Datos de viento",
    "terreno": "3 · Terreno y rugosidad",
    "turbina": "4 · Aerogenerador",
    "simulacion": "5 · Simulación",
    "proyecto": "Proyecto completo",
}

# nombre del archivo de cada paso dentro del ZIP del proyecto
PROJECT_MEMBERS = {
    "layout": "1_layout.json",
    "viento": "2_viento.zip",
    "terreno": "3_terreno.zip",
    "turbina": "4_turbina.json",
    "simulacion": "5_simulacion.json",
}


# ------------------------------------------------------------------ utilidades
def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def _header(kind: str) -> dict:
    return {"formato": FORMAT, "tipo": kind, "version": VERSION, "vientomx": __version__,
            "creado": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _dumps(kind: str, payload: dict) -> bytes:
    return json.dumps({**_header(kind), **_jsonable(payload)}, ensure_ascii=False, indent=2).encode("utf-8")


def _read_bytes(src) -> bytes:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    if hasattr(src, "getvalue"):
        return src.getvalue()
    if hasattr(src, "read"):
        return src.read()
    with open(src, "rb") as f:
        return f.read()


def _check(js: dict, kind: str) -> dict:
    if not isinstance(js, dict) or js.get("formato") != FORMAT:
        raise ValueError("El archivo no es una configuración de iik.")
    got = js.get("tipo")
    if got != kind:
        raise ValueError(f"Este archivo es de «{KIND_NAMES.get(got, got)}», no de «{KIND_NAMES.get(kind, kind)}».")
    if int(js.get("version", 0)) > VERSION:
        raise ValueError("El archivo se creó con una versión más nueva de iik; actualice el programa.")
    return js


def _loads(src, kind: str) -> dict:
    try:
        js = json.loads(_read_bytes(src).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"No es un archivo JSON válido: {e}") from None
    return _check(js, kind)


def _open_zip(src, kind: str) -> tuple[zipfile.ZipFile, dict]:
    try:
        z = zipfile.ZipFile(io.BytesIO(_read_bytes(src)))
    except zipfile.BadZipFile:
        raise ValueError("No es un archivo ZIP válido.") from None
    if "config.json" not in z.namelist():
        raise ValueError("El ZIP no contiene config.json; no parece una configuración de iik.")
    return z, _check(json.loads(z.read("config.json").decode("utf-8-sig")), kind)


def detect_kind(src) -> str | None:
    """Tipo de configuración de un archivo (``'layout'``, ``'viento'``…) o None si no se reconoce."""
    data = _read_bytes(src)
    try:
        if data[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(data))
            if "config.json" in z.namelist():
                js = json.loads(z.read("config.json").decode("utf-8-sig"))
            elif any(n in z.namelist() for n in PROJECT_MEMBERS.values()):
                return "proyecto"
            else:
                return None
        else:
            js = json.loads(data.decode("utf-8-sig"))
        return js.get("tipo") if js.get("formato") == FORMAT else None
    except Exception:
        return None


def _df_to_records(df: pd.DataFrame | None):
    if df is None:
        return None
    return {"columns": [str(c) for c in df.columns], "data": json.loads(df.to_json(orient="values")),
            "attrs": _jsonable(dict(df.attrs))}


def _df_from_records(d) -> pd.DataFrame | None:
    if not d:
        return None
    df = pd.DataFrame(d["data"], columns=d["columns"])
    df.attrs.update(d.get("attrs") or {})
    return df


# ------------------------------------------------------------------ 1 · layout
def layout_to_json(layout: pd.DataFrame, map_center=None, map_zoom=None) -> bytes:
    return _dumps("layout", {"turbinas": json.loads(layout.to_json(orient="records")),
                             "mapa": {"centro": map_center, "zoom": map_zoom}})


def layout_from_json(src) -> tuple[pd.DataFrame, dict]:
    """Devuelve (layout, extras) donde extras = {"centro": [lat, lon] | None, "zoom": int | None}."""
    from .layout import _validate

    js = _loads(src, "layout")
    df = pd.DataFrame(js.get("turbinas") or [])
    if df.empty or not {"lat", "lon"} <= set(df.columns):
        raise ValueError("El archivo no contiene turbinas con lat/lon.")
    if "id" not in df:
        df.insert(0, "id", [f"WTG{i + 1:02d}" for i in range(len(df))])
    df["id"] = df["id"].astype(str)
    df["lat"], df["lon"] = pd.to_numeric(df.lat), pd.to_numeric(df.lon)
    df = df.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    _validate(df)
    return df, dict(js.get("mapa") or {})


# ------------------------------------------------------------------ 2 · viento
def _met_meta(m: MetData) -> dict:
    return {"lat": m.lat, "lon": m.lon, "fuente": m.source, "elevacion": m.elevation, "notas": list(m.notes)}


def _met_csv(m: MetData) -> bytes:
    return m.df.to_csv(index_label="time_utc", float_format="%.4g").encode()


def _met_from_csv(data: bytes, meta: dict) -> MetData:
    df = pd.read_csv(io.BytesIO(data), index_col="time_utc")
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = None
    df = df.astype(float)
    return MetData(df, float(meta["lat"]), float(meta["lon"]), str(meta["fuente"]),
                   None if meta.get("elevacion") is None else float(meta["elevacion"]), list(meta.get("notas") or []))


def met_to_zip(met: MetData, reanalysis: MetData | None = None, ref_latlon=None, calib=None, calib_h=None,
               calib_ref=None, gwa_info=None, mcp_info: pd.DataFrame | None = None, ui: dict | None = None) -> bytes:
    """Serie de viento del paso 2 (y la del reanálisis si se aplicó MCP) más la calibración y las opciones."""
    same = reanalysis is None or reanalysis is met
    cfg = {**_header("viento"),
           **_jsonable({"serie": _met_meta(met), "reanalisis": None if same else _met_meta(reanalysis),
                        "reanalisis_es_serie": reanalysis is met,
                        "punto_referencia": ref_latlon,
                        "calibracion": {"velocidad": calib, "altura": calib_h, "punto": calib_ref, "gwa": gwa_info},
                        "mcp": _df_to_records(mcp_info), "opciones": ui or {}})}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
        z.writestr("serie.csv", _met_csv(met))
        if not same:
            z.writestr("reanalisis.csv", _met_csv(reanalysis))
        z.writestr("LEAME.txt", "Configuración del paso 2 (datos de viento) de iik.\n"
                                "serie.csv: serie horaria usada en el estudio (UTC; ws_<altura> en m/s, wd_<altura> en °).\n"
                                "reanalisis.csv: serie de reanálisis usada para el MCP (si aplica).\n"
                                "config.json: fuente, punto, calibración y opciones.\n")
    return buf.getvalue()


def met_from_zip(src) -> dict:
    """Devuelve un dict con met, met_reanalysis, ref_latlon, calib, calib_h, calib_ref, gwa_info, mcp_info, ui."""
    z, cfg = _open_zip(src, "viento")
    if "serie.csv" not in z.namelist():
        raise ValueError("El ZIP no contiene serie.csv.")
    m = _met_from_csv(z.read("serie.csv"), cfg["serie"])
    if cfg.get("reanalisis") and "reanalisis.csv" in z.namelist():
        rean = _met_from_csv(z.read("reanalisis.csv"), cfg["reanalisis"])
    else:
        rean = m if cfg.get("reanalisis_es_serie") else None
    cal = cfg.get("calibracion") or {}
    tup = lambda v: tuple(float(x) for x in v) if v else None  # noqa: E731
    return {"met": m, "met_reanalysis": rean, "ref_latlon": tup(cfg.get("punto_referencia")),
            "calib": cal.get("velocidad"), "calib_h": cal.get("altura"), "calib_ref": tup(cal.get("punto")),
            "gwa_info": cal.get("gwa"), "mcp_info": _df_from_records(cfg.get("mcp")), "ui": cfg.get("opciones") or {}}


# ------------------------------------------------------------------ 3 · terreno
def grid_to_zip(grid: Grid, ui: dict | None = None) -> bytes:
    cfg = {**_header("terreno"),
           **_jsonable({"crs_wkt": grid.crs.to_wkt(), "crs_nombre": grid.crs.name, "x0": grid.x0, "y0": grid.y0,
                        "dx": grid.dx, "nx": grid.nx, "ny": grid.ny, "meta": grid.meta, "opciones": ui or {}})}
    arrays = {k: getattr(grid, k) for k in ("elev", "z0", "landcover") if getattr(grid, k) is not None}
    npz = io.BytesIO()
    np.savez_compressed(npz, **arrays)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # el npz ya va comprimido
        z.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
        z.writestr("malla.npz", npz.getvalue())
    return buf.getvalue()


def grid_from_zip(src) -> tuple[Grid, dict]:
    from pyproj import CRS

    z, cfg = _open_zip(src, "terreno")
    if "malla.npz" not in z.namelist():
        raise ValueError("El ZIP no contiene malla.npz.")
    g = Grid(CRS.from_wkt(cfg["crs_wkt"]), float(cfg["x0"]), float(cfg["y0"]), float(cfg["dx"]),
             int(cfg["nx"]), int(cfg["ny"]), meta=dict(cfg.get("meta") or {}))
    with np.load(io.BytesIO(z.read("malla.npz")), allow_pickle=False) as npz:
        for k in ("elev", "z0", "landcover"):
            if k in npz.files:
                a = npz[k]
                if a.shape != (g.ny, g.nx):
                    raise ValueError(f"La matriz '{k}' tiene forma {a.shape}, se esperaba {(g.ny, g.nx)}.")
                setattr(g, k, a)
    return g, dict(cfg.get("opciones") or {})


# ------------------------------------------------------------------ 4 · aerogenerador
def turbine_to_json(t: Turbine, hub_height: float | None = None) -> bytes:
    return _dumps("turbina", {"nombre": t.name, "potencia_nominal_kw": t.rated_kw, "diametro_rotor_m": t.rotor_diameter,
                              "fuente": t.source, "ct_estimado": t.ct_estimated, "alturas_buje": list(t.hub_heights),
                              "altura_buje": hub_height,
                              "curva": {"velocidad_ms": t.ws, "potencia_kw": t.power_kw, "ct": t.ct}})


def turbine_from_json(src) -> tuple[Turbine, float | None]:
    js = _loads(src, "turbina")
    c = js["curva"]
    ct = c.get("ct")
    t = Turbine(str(js["nombre"]), float(js.get("potencia_nominal_kw") or 0), float(js["diametro_rotor_m"]),
                np.asarray(c["velocidad_ms"], float), np.asarray(c["potencia_kw"], float),
                None if ct is None else np.asarray(ct, float), str(js.get("fuente") or ""),
                hub_heights=[float(h) for h in js.get("alturas_buje") or []])
    if ct is not None:
        t.ct_estimated = bool(js.get("ct_estimado"))
    hh = js.get("altura_buje")
    return t, None if hh is None else float(hh)


# ------------------------------------------------------------------ 5 · simulación
SIM_KEYS = ("wake_model", "wake_k", "ti", "use_terrain", "use_roughness", "blending_height", "air_density_auto",
            "air_density", "losses", "uncertainty")


def sim_to_json(settings: dict) -> bytes:
    """``settings``: wake_model, wake_k, ti, use_terrain, use_roughness, blending_height, air_density_auto,
    air_density, losses {concepto: %}, uncertainty {componente: [tipo, valor]}."""
    return _dumps("simulacion", {k: settings.get(k) for k in SIM_KEYS})


def sim_from_json(src) -> dict:
    js = _loads(src, "simulacion")
    out = {k: js[k] for k in SIM_KEYS if js.get(k) is not None}
    if "losses" in out:
        out["losses"] = {str(k): float(v) for k, v in out["losses"].items()}
    if "uncertainty" in out:
        out["uncertainty"] = {str(k): (str(v[0]), float(v[1])) for k, v in out["uncertainty"].items()}
    if out.get("wake_model") not in (None, "jensen", "bastankhah", "none"):
        raise ValueError(f"Modelo de estelas desconocido: {out['wake_model']}")
    return out


# ------------------------------------------------------------------ proyecto completo
def project_to_zip(parts: dict[str, bytes]) -> bytes:
    """Empaqueta los archivos de cada paso (los que existan) en un solo ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("proyecto.json", _dumps("proyecto", {"pasos": [k for k in PROJECT_MEMBERS if parts.get(k)]}))
        for k, name in PROJECT_MEMBERS.items():
            if parts.get(k):
                z.writestr(name, parts[k])
    return buf.getvalue()


def project_from_zip(src) -> dict[str, bytes]:
    """Devuelve {paso: bytes} con los archivos de cada paso incluidos en el ZIP del proyecto."""
    try:
        z = zipfile.ZipFile(io.BytesIO(_read_bytes(src)))
    except zipfile.BadZipFile:
        raise ValueError("No es un archivo ZIP válido.") from None
    if "proyecto.json" in z.namelist():
        _check(json.loads(z.read("proyecto.json").decode("utf-8-sig")), "proyecto")
    out = {k: z.read(name) for k, name in PROJECT_MEMBERS.items() if name in z.namelist()}
    if not out:
        raise ValueError("El ZIP no contiene archivos de pasos de iik.")
    return out
