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

import calendar
import concurrent.futures as cf
import json
import os
import threading
from pathlib import Path

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


def _decode(raw: np.ndarray, attrs) -> np.ndarray:
    x = raw.astype(np.float64)
    fill = attrs.get("fill_value")
    if fill is not None:
        x[raw == fill] = np.nan
    return x / float(attrs.get("scale_factor", 1.0))


# --------------------------------------------------------------------------
# Lectura rápida: índice de chunks reutilizado entre años
# --------------------------------------------------------------------------
# Los archivos 2007–2013 (v1.0.0) tienen exactamente la misma estructura interna; solo los
# años bisiestos son 480 bytes más largos (24 h más en time_index), y todo lo posterior queda
# desplazado esa cantidad. Por eso basta buscar los chunks en el índice B-tree (lo lento) de un
# año de referencia y aplicar a los demás el desplazamiento = diferencia de tamaño de archivo.
REF_YEAR = {"v1.0.0": 2007, "v1.1.0": 2014}
_LAYOUT_LOCK = threading.Lock()


def _version(y: int) -> str:
    return "v1.1.0" if y >= 2014 else "v1.0.0"


class _Src:
    """Archivo WTK remoto (URL) o local (pruebas): tamaño, lectura de rangos y apertura con h5py."""

    _sessions: dict = {}

    def __init__(self, src):
        self.src = str(src)
        self.remote = self.src.startswith(("http://", "https://"))
        if not self.remote:
            self._fh, self._lock = open(self.src, "rb"), threading.Lock()

    @property
    def ses(self):
        tid = threading.get_ident()
        if tid not in self._sessions:
            sess = requests.Session()
            sess.headers.update(UA)
            sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=4, max_retries=3))
            self._sessions[tid] = sess
        return self._sessions[tid]

    def size(self) -> int:
        if not self.remote:
            return os.path.getsize(self.src)
        r = self.ses.head(self.src, timeout=60)
        if r.status_code != 200:
            raise DownloadError(f"No se pudo abrir {self.src} (HTTP {r.status_code}).")
        return int(r.headers["Content-Length"])

    def get_range(self, start: int, length: int) -> bytes:
        if not self.remote:
            with self._lock:
                self._fh.seek(start)
                return self._fh.read(length)
        for attempt in range(3):
            try:
                r = self.ses.get(self.src, headers={"Range": f"bytes={start}-{start + length - 1}"}, timeout=120)
                if r.status_code in (200, 206) and len(r.content) == length:
                    return r.content
            except requests.RequestException:
                if attempt == 2:
                    raise
        raise DownloadError(f"Lectura parcial fallida en {self.src} (HTTP {r.status_code}).")

    def h5(self):
        return _open(self.src)


def _layout_path() -> Path:
    return CACHE_DIR / "wtk_layout.json"


def _load_layouts() -> dict:
    p = _layout_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _save_layout(key: str, value: dict):
    with _LAYOUT_LOCK:
        lay = _load_layouts()
        lay[key] = value
        p = _layout_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(lay))


def _column_layout(ref: _Src, var: str, gid: int, stride_hint=None) -> dict:
    """Índice de chunks de la columna que contiene ``gid`` en el archivo de referencia (con caché)."""
    lay = _load_layouts()
    shape_key = f"{ref.src}|{var}|chunks"
    if shape_key in lay:
        cs = lay[shape_key][1]
        key = f"{ref.src}|{var}|{(gid // cs) * cs}"
        if key in lay:
            return lay[key]
    h, _ = ref.h5()
    d = h[var]
    ct, cs = d.chunks
    c0 = (gid // cs) * cs
    key = f"{ref.src}|{var}|{c0}"
    if d.compression is not None:
        raise DownloadError("Formato WTK inesperado (datos comprimidos).")
    offs, stride = _column_offsets(d, c0, stride_hint)
    value = {"offs": [int(o) for o in offs], "ct": int(ct), "cs": int(cs), "c0": int(c0), "dtype": d.dtype.str,
             "scale": float(d.attrs.get("scale_factor", 1.0)),
             "fill": None if d.attrs.get("fill_value") is None else int(d.attrs["fill_value"]),
             "ref_size": ref.size(), "nt_ref": int(d.shape[0]), "stride": stride}
    _save_layout(shape_key, [int(ct), int(cs)])
    _save_layout(key, value)
    return value


def _nt(year: int) -> int:
    return 8784 if (calendar.isleap(year) and _version(year) == "v1.0.0") else 8760


def _fetch_columns(src_for_year, years, variables, gids, progress: ProgressCb = None, max_workers=12) -> dict:
    """{(año, var, gid): serie decodificada}, descargando solo los chunks necesarios (en paralelo)."""
    gids = [int(g) for g in np.atleast_1d(gids)]
    versions = sorted({_version(y) for y in years})
    refs = {ver: _Src(src_for_year(REF_YEAR[ver])) for ver in versions}
    layouts, stride = {}, None
    steps = [(ver, var, g) for ver in versions for var in variables for g in gids]
    for k, (ver, var, g) in enumerate(steps):
        if progress:
            progress(0.15 * k / len(steps), f"Indexando bloques de {var}; solo la primera vez para este punto, "
                                            "puede tardar unos minutos…")
        layouts[(ver, var, g)] = lay = _column_layout(refs[ver], var, g, stride)
        stride = lay.get("stride") or stride
    srcs = {y: refs[_version(y)] if src_for_year(y) == refs[_version(y)].src else _Src(src_for_year(y)) for y in years}
    sizes = {y: srcs[y].size() for y in years}
    jobs = {}
    for y in years:
        for var in variables:
            for g in gids:
                lay = layouts[(_version(y), var, g)]
                n_chunks = -(-_nt(y) // lay["ct"])
                if n_chunks != len(lay["offs"]):
                    raise DownloadError(f"Estructura WTK inesperada en {y} ({var}).")
                delta = sizes[y] - lay["ref_size"]
                for i in range(n_chunks):
                    jobs[(y, var, lay["c0"], i)] = (lay["offs"][i] + delta, lay)
    nbytes = sum(lay["ct"] * lay["cs"] * np.dtype(lay["dtype"]).itemsize for _, lay in jobs.values())
    def fetch(key):
        off, lay = jobs[key]
        size = lay["ct"] * lay["cs"] * np.dtype(lay["dtype"]).itemsize
        blk = np.frombuffer(srcs[key[0]].get_range(off, size), dtype=lay["dtype"]).reshape(lay["ct"], lay["cs"])
        return {g: blk[:, g - lay["c0"]].copy() for g in gids if lay["c0"] <= g < lay["c0"] + lay["cs"]}

    parts = {}
    with cf.ThreadPoolExecutor(max_workers) as ex:  # el avance se informa desde el hilo principal (Streamlit)
        futs = {ex.submit(fetch, k): k for k in jobs}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            parts[futs[fut]] = fut.result()
            if progress:
                progress(0.15 + 0.85 * n / len(jobs),
                         f"Descargando bloques {n}/{len(jobs)} ({nbytes * n / len(jobs) / 1e6:.0f} de "
                         f"{nbytes / 1e6:.0f} MB)…")
    out = {}
    for y in years:
        for var in variables:
            for g in gids:
                lay = layouts[(_version(y), var, g)]
                n_chunks = -(-_nt(y) // lay["ct"])
                raw = np.concatenate([parts[(y, var, lay["c0"], i)][g] for i in range(n_chunks)])[:_nt(y)]
                attrs = {"scale_factor": lay["scale"], "fill_value": lay["fill"]}
                if not _plausible(var, raw, attrs):  # índice no aplicable a este año: lectura directa (lenta)
                    h, _ = srcs[y].h5()
                    raw = h[var][:, g]
                out[(y, var, g)] = _decode(raw, attrs)
    return out


def compare_points(gids, year: int = 2013, height: int = 100, src=None, progress: ProgressCb = None
                   ) -> pd.DataFrame:
    """Velocidad media de un año en varios puntos de malla (para elegir el representativo)."""
    src_for_year = (lambda y: src) if src else year_url
    var = f"windspeed_{height}m"
    gids = [int(g) for g in gids]
    got = _fetch_columns(src_for_year, [year], [var], gids, progress)
    return pd.DataFrame({"gid": gids, f"v_media_{year}_{height}m": [float(np.nanmean(got[(year, var, g)])) for g in gids]})


def _point_meta(src, gid: int) -> dict:
    h, _ = _open(src)
    m = h["meta"][gid]
    return {"lat": float(m["latitude"]), "lon": float(m["longitude"]), "elevation": float(m["elevation"])}


def download_wtk_mexico(lat: float, lon: float, start_year: int = 2007, end_year: int = 2013,
                        hub_height: float = 100.0, gid: int | None = None, progress: ProgressCb = None,
                        use_cache: bool = True, src_for_year=None) -> MetData:
    """Serie horaria WTK-México en el punto de malla ``gid`` (o el más cercano a lat/lon).

    Descarga velocidad en dos alturas alrededor del buje, dirección en la superior,
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
    wind_vars = {f"windspeed_{h_lo}m": f"ws_{h_lo}", f"windspeed_{h_hi}m": f"ws_{h_hi}",
                 f"winddirection_{h_dir}m": f"wd_{h_dir}"}
    tp_vars = {"temperature_2m": "t2m", "pressure_0m": "ps"}
    cpath = lambda y, tp: CACHE_DIR / f"wtk_mexico_{gid}_{y}_{h_lo}_{h_hi}{'_tp' if tp else ''}.csv.gz"  # noqa: E731
    mpath = CACHE_DIR / f"wtk_mexico_{gid}_meta.json"
    frames = {}
    todo = [y for y in years if not (use_cache and cpath(y, False).exists())]
    tp_year = years[0]
    need_tp = not (use_cache and cpath(tp_year, True).exists())
    try:
        if todo or need_tp:
            got = {}
            # T/p solo para el primer año; el viento para todos los años pendientes
            if todo:
                got.update(_fetch_columns(src_for_year, todo, list(wind_vars), [gid], progress))
            if need_tp:
                if progress:
                    progress(0.99, "Temperatura y presión (densidad del aire)…")
                got.update(_fetch_columns(src_for_year, [tp_year], list(tp_vars), [gid], None))
            for y in set(todo) | ({tp_year} if need_tp else set()):
                idx = pd.date_range(f"{y}-01-01", periods=_nt(y), freq="h", tz="UTC")
                if y in todo:
                    dfw = pd.DataFrame({wind_vars[v]: got[(y, v, gid)] for v in wind_vars}, index=idx)
                    if use_cache:
                        cpath(y, False).parent.mkdir(parents=True, exist_ok=True)
                        dfw.to_csv(cpath(y, False))
                    frames[y] = dfw
                if need_tp and y == tp_year:
                    dft = pd.DataFrame({tp_vars[v]: got[(y, v, gid)] for v in tp_vars}, index=idx)
                    dft["ps"] = dft["ps"] / 100.0  # Pa -> hPa
                    if use_cache:
                        dft.to_csv(cpath(y, True))
                    frames[("tp", y)] = dft
        if use_cache and mpath.exists():
            meta = json.loads(mpath.read_text())
        else:
            meta = _point_meta(src_for_year(years[0]), gid)
            if use_cache:
                mpath.parent.mkdir(parents=True, exist_ok=True)
                mpath.write_text(json.dumps(meta))
    except DownloadError:
        raise
    except Exception as e:  # red, proxy, etc.
        raise DownloadError(f"WIND Toolkit: {type(e).__name__}: {e}") from e

    def _read(p):
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
        return df

    wind = pd.concat([frames[y] if y in frames else _read(cpath(y, False)) for y in years]).sort_index()
    tp = frames.get(("tp", tp_year))
    if tp is None:
        tp = _read(cpath(tp_year, True))
    df = wind.join(tp, how="left")
    df = df[~df.index.duplicated()].dropna(how="all", subset=list(wind_vars.values()))
    if progress:
        progress(1.0, "WIND Toolkit descargado")
    glat, glon, gelev = meta["lat"], meta["lon"], meta["elevation"]
    # La elevación de "meta" es la del terreno real en el punto, no la del terreno suavizado del WRF; para
    # corregir la densidad se usa la altura equivalente a la presión superficial media (atmósfera estándar).
    z_eff = gelev
    if "ps" in df and df["ps"].notna().any():
        z_eff = float(44330.8 * (1 - (df["ps"].mean() / 1013.25) ** 0.190263))
    return MetData(df, glat, glon, f"{SOURCE}, punto {gid}", z_eff,
                   [f"Punto de malla WTK {gid} ({glat:.4f}, {glon:.4f}); elevación del terreno {gelev:.0f} m, "
                    f"altura equivalente a la presión del modelo {z_eff:.0f} m"])
