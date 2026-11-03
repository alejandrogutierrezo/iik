"""NREL WIND Toolkit México (WTK-Mexico): series horarias de un modelo de mesoescala.

Simulación WRF de 3Tier/Vaisala para el NREL con malla de ~2 km, horaria,
2007–2014, viento a 10–200 m. Mucho más fina que ERA5 (~28 km) o MERRA-2
(~50 km): resuelve mejor los pasos de montaña, cañones y brisas.

Los datos están en el bucket público ``s3://nrel-pds-wtk/mexico`` (AWS Open Data),
sin registro. Cada archivo anual pesa ~1 TB, pero aquí solo se descargan los
bloques ("chunks") de HDF5 que contienen el punto elegido (~14 MB por variable y año;
~54 MB en 2014, que usa otra forma de chunk), en paralelo con peticiones HTTP de rango.

Cita: Draxl, C., Hodge, B.M., Clifton, A., McCaa, J. (2015). The Wind Integration
National Dataset (WIND) Toolkit. Applied Energy 151: 355-366.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import threading

import numpy as np
import pandas as pd
import requests

from .met import CACHE_DIR, UA, DownloadError, MetData, ProgressCb

BASE_URL = os.environ.get("VIENTOMX_WTK_URL", "https://nrel-pds-wtk.s3.amazonaws.com/mexico")
YEARS = tuple(range(2007, 2015))
HEIGHTS = (10, 40, 60, 80, 100, 120, 140, 160, 200)
SOURCE = "NREL WIND Toolkit México (WRF ~2 km)"


def year_url(year: int) -> str:
    if year not in YEARS:
        raise ValueError(f"WTK-México cubre {YEARS[0]}–{YEARS[-1]}; año pedido: {year}.")
    ver = "v1.1.0" if year >= 2014 else "v1.0.0"
    return f"{BASE_URL}/{ver}/wtk_mexico_{year}.h5"


class RangeFile:
    """Archivo remoto de solo lectura sobre HTTP Range, con caché de bloques (para h5py)."""

    def __init__(self, url: str, block_size: int = 1 << 20, session=None, timeout=60):
        self.url, self.bs, self.timeout = url, block_size, timeout
        self.ses = session or requests.Session()
        self.ses.headers.update(UA)
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=3)
        self.ses.mount("https://", adapter)
        self.ses.mount("http://", adapter)
        r = self.ses.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code != 200 or "Content-Length" not in r.headers:
            raise DownloadError(f"No se pudo abrir {url} (HTTP {r.status_code}).")
        self.size = int(r.headers["Content-Length"])
        self.pos, self._cache = 0, {}

    # interfaz de archivo
    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def get_range(self, start: int, length: int) -> bytes:
        r = self.ses.get(self.url, headers={"Range": f"bytes={start}-{start + length - 1}"}, timeout=self.timeout)
        if r.status_code not in (200, 206) or len(r.content) != length:
            raise DownloadError(f"Lectura parcial fallida en {self.url} (HTTP {r.status_code}).")
        return r.content

    def _block(self, i):
        if i not in self._cache:
            a = i * self.bs
            self._cache[i] = self.get_range(a, min(self.bs, self.size - a))
        return self._cache[i]

    def read(self, n=-1):
        end = self.size if n is None or n < 0 else min(self.pos + n, self.size)
        out, p = bytearray(), self.pos
        while p < end:
            i = p // self.bs
            blk = self._block(i)
            o = p - i * self.bs
            take = min(len(blk) - o, end - p)
            out += blk[o:o + take]
            p += take
        self.pos = p
        return bytes(out)

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)

    def close(self):
        self._cache.clear()


def _open(src):
    """``src`` = URL (remoto) o ruta local (pruebas). Devuelve (h5py.File, lector de bytes)."""
    import h5py

    if str(src).startswith(("http://", "https://")):
        f = RangeFile(src)
        return h5py.File(f, "r"), f.get_range
    fh = open(src, "rb")
    lock = threading.Lock()

    def get_range(start, length):
        with lock:  # el mismo descriptor se comparte entre hilos
            fh.seek(start)
            return fh.read(length)

    return h5py.File(fh, "r"), get_range


def _coords(h, cache_key: str | None) -> np.ndarray:
    p = CACHE_DIR / f"wtk_{cache_key}_coords.npy" if cache_key else None
    if p is not None and p.exists():
        return np.load(p)
    c = h["coordinates"][:].astype(np.float32)
    if p is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, c)
    return c


def nearest_points(lat: float, lon: float, n: int = 9, src=None) -> pd.DataFrame:
    """Puntos de malla WTK más cercanos: índice, lat, lon, distancia [km] y elevación del modelo [m]."""
    src = src or year_url(YEARS[0])
    h, _ = _open(src)
    c = _coords(h, "mexico" if str(src).startswith("http") else None)
    d = np.hypot(c[:, 0] - lat, (c[:, 1] - lon) * np.cos(np.radians(lat))) * 111.2
    idx = np.argsort(d)[:n]
    meta = h["meta"]
    rows = []
    for i in idx:
        m = meta[int(i)]
        rows.append({"gid": int(i), "lat": float(c[i, 0]), "lon": float(c[i, 1]), "dist_km": float(d[i]),
                     "elev_m": float(m["elevation"]),
                     "municipio": m["county"].decode("utf-8", "replace") if isinstance(m["county"], bytes) else ""})
    return pd.DataFrame(rows)


def heights_for_hub(hub: float) -> tuple[int, int]:
    """Dos alturas del WTK que rodean al buje (para cizalladura)."""
    hs = np.array(HEIGHTS)
    lo = hs[hs <= hub].max() if (hs <= hub).any() else hs[0]
    hi = hs[hs >= hub].min() if (hs >= hub).any() else hs[-1]
    if lo == hi:
        j = int(np.where(hs == lo)[0][0])
        lo, hi = (hs[j - 1], hs[j]) if j > 0 else (hs[0], hs[1])
    return int(lo), int(hi)


def _column_offsets(d, c0: int, stride_hint: int | None = None) -> tuple[list[int], int | None]:
    """Desplazamientos en bytes de los chunks (todos los tiempos) de la columna ``c0``.

    Buscar cada chunk en el índice B-tree de HDF5 es lo lento (varias lecturas remotas por
    búsqueda). Los archivos WTK se escribieron en orden: los chunks de una columna están
    equiespaciados, así que bastan 2–3 búsquedas para deducir el resto. Si el patrón no
    se cumple se buscan todos."""
    nt = d.shape[0]
    ct = d.chunks[0]
    t0s = list(range(0, nt, ct))
    look = lambda t0: d.id.get_chunk_info_by_coord((t0, c0)).byte_offset  # noqa: E731
    first = look(t0s[0])
    if len(t0s) == 1:
        return [first], None
    if stride_hint is None:
        stride = look(t0s[1]) - first
    else:
        stride = stride_hint
    last = look(t0s[-1])
    if last == first + (len(t0s) - 1) * stride:
        return [first + i * stride for i in range(len(t0s))], stride
    return [look(t0) for t0 in t0s], None


def _plausible(var: str, x: np.ndarray, attrs) -> bool:
    v = _decode(x, attrs)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return False
    lo, hi = ((0, 80) if var.startswith("windspeed") else (0, 360.01) if var.startswith("winddirection")
              else (-60, 70) if var.startswith("temperature") else (3e4, 1.1e5) if var.startswith("pressure")
              else (-np.inf, np.inf))
    return float(np.mean((v < lo) | (v > hi))) < 0.01


def _read_many(h, get_range, requests_: list[tuple[str, list[int]]], max_workers=12) -> dict:
    """Series completas para varias (variable, gids), leyendo solo sus chunks, todos en paralelo.
    Devuelve {(var, gid): ndarray crudo}."""
    plan, jobs = {}, {}
    stride = None
    for var, gids in requests_:
        d = h[var]
        nt, _ = d.shape
        ct, cs = d.chunks
        plan[var] = (d, nt, ct, cs, gids)
        if d.compression is not None:
            continue
        size = int(ct * cs * d.dtype.itemsize)
        for c0 in sorted({(g // cs) * cs for g in gids}):
            offs, st = _column_offsets(d, c0, stride)
            stride = st or stride
            for t0, off in zip(range(0, nt, ct), offs):
                jobs[(var, t0, c0)] = (off, size)

    def fetch(key):
        d, nt, ct, cs, _ = plan[key[0]]
        off, size = jobs[key]
        return key, np.frombuffer(get_range(off, size), dtype=d.dtype).reshape(ct, cs)

    with cf.ThreadPoolExecutor(max_workers) as ex:
        blocks = dict(ex.map(fetch, list(jobs)))
    out = {}
    for var, (d, nt, ct, cs, gids) in plan.items():
        for g in gids:
            if d.compression is not None:  # no esperado en WTK: lectura normal (más lenta)
                out[(var, g)] = d[:, g]
                continue
            c0 = (g // cs) * cs
            col = np.concatenate([blocks[(var, t0, c0)][:, g - c0] for t0 in range(0, nt, ct)])[:nt]
            if not _plausible(var, col, d.attrs):  # patrón deducido incorrecto: lectura directa con h5py
                col = d[:, g]
            out[(var, g)] = col
    return out


def compare_points(gids, year: int = 2013, height: int = 100, src=None) -> pd.DataFrame:
    """Velocidad media de un año en varios puntos de malla (para elegir el representativo)."""
    h, get_range = _open(src or year_url(year))
    var = f"windspeed_{height}m"
    raw = _read_many(h, get_range, [(var, list(map(int, gids)))])
    attrs = h[var].attrs
    return pd.DataFrame({"gid": list(map(int, gids)),
                         f"v_media_{year}_{height}m": [float(np.nanmean(_decode(raw[(var, int(g))], attrs))) for g in gids]})


def _decode(raw: np.ndarray, attrs) -> np.ndarray:
    x = raw.astype(np.float64)
    fill = attrs.get("fill_value")
    if fill is not None:
        x[raw == fill] = np.nan
    return x / float(attrs.get("scale_factor", 1.0))


def download_wtk_mexico(lat: float, lon: float, start_year: int = 2007, end_year: int = 2014,
                        hub_height: float = 100.0, gid: int | None = None, progress: ProgressCb = None,
                        use_cache: bool = True, src_for_year=None) -> MetData:
    """Serie horaria WTK-México en el punto de malla ``gid`` (o el más cercano a lat/lon).

    Descarga velocidad en dos alturas alrededor del buje, dirección en la más cercana,
    y T2m / presión superficial del primer año (para la densidad del aire).
    """
    src_for_year = src_for_year or year_url
    years = list(range(int(start_year), int(end_year) + 1))
    for y in years:
        if y not in YEARS:
            raise ValueError(f"WTK-México cubre {YEARS[0]}–{YEARS[-1]}; ajuste los años.")
    h_lo, h_hi = heights_for_hub(hub_height)
    h_dir = h_hi  # la cizalladura por sector usa la dirección de la altura superior
    if gid is None:
        gid = int(nearest_points(lat, lon, 1, src=src_for_year(years[0])).gid.iloc[0])
    variables = {f"windspeed_{h_lo}m": f"ws_{h_lo}", f"windspeed_{h_hi}m": f"ws_{h_hi}",
                 f"winddirection_{h_dir}m": f"wd_{h_dir}", "temperature_2m": "t2m", "pressure_0m": "ps"}
    frames, glat, glon, gelev = [], lat, lon, None
    for k, y in enumerate(years):
        if progress:
            progress(k / len(years), f"Descargando WIND Toolkit {y} (punto {gid})…")
        cpath = CACHE_DIR / f"wtk_mexico_{gid}_{y}_{h_lo}_{h_hi}{'_tp' if k == 0 else ''}.csv.gz"
        if use_cache and cpath.exists():
            df = pd.read_csv(cpath, index_col=0, parse_dates=True)
            df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
            meta = json.loads(cpath.with_suffix(".json").read_text())
        else:
            try:
                h, get_range = _open(src_for_year(y))
                ti = pd.to_datetime([t.decode() if isinstance(t, bytes) else t for t in h["time_index"][:]], utc=True)
                wanted = [v for v in variables if k == 0 or v not in ("temperature_2m", "pressure_0m")]
                raw = _read_many(h, get_range, [(v, [gid]) for v in wanted])
                cols = {variables[v]: _decode(raw[(v, gid)], h[v].attrs) for v in wanted}
                df = pd.DataFrame(cols, index=ti)
                if "ps" in df:  # T y presión solo del primer año (bastan para la densidad media)
                    df["ps"] = df["ps"] / 100.0  # Pa -> hPa
                m = h["meta"][gid]
                meta = {"lat": float(m["latitude"]), "lon": float(m["longitude"]), "elevation": float(m["elevation"])}
            except DownloadError:
                raise
            except Exception as e:  # red, proxy, etc.
                raise DownloadError(f"WIND Toolkit {y}: {type(e).__name__}: {e}") from e
            if use_cache:
                cpath.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(cpath)
                cpath.with_suffix(".json").write_text(json.dumps(meta))
        glat, glon, gelev = meta["lat"], meta["lon"], meta["elevation"]
        frames.append(df)
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated()].dropna(how="all")
    if progress:
        progress(1.0, "WIND Toolkit descargado")
    # La elevación de "meta" es la del terreno real en el punto, no la del terreno suavizado del WRF; para
    # corregir la densidad se usa la altura equivalente a la presión superficial media (atmósfera estándar).
    z_eff = gelev
    if "ps" in df and df["ps"].notna().any():
        z_eff = float(44330.8 * (1 - (df["ps"].mean() / 1013.25) ** 0.190263))
    return MetData(df, glat, glon, f"{SOURCE}, punto {gid}", z_eff,
                   [f"Punto de malla WTK {gid} ({glat:.4f}, {glon:.4f}); elevación del terreno {gelev:.0f} m, "
                    f"altura equivalente a la presión del modelo {z_eff:.0f} m"])
