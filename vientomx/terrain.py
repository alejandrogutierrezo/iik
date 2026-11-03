"""Terreno y rugosidad: descarga, malla UTM local y modelos de flujo.

Datos (descarga directa, sin registro, cobertura global incluyendo México):

* Elevación: Copernicus DEM GLO-30 (30 m) en AWS Open Data.
* Cobertura de suelo: ESA WorldCover 2021 (10 m) en AWS Open Data, convertida
  a longitud de rugosidad z0.
* Alternativa: GeoTIFF propio (p. ej. INEGI Continuo de Elevaciones Mexicano
  CEM 3.0 a 15 m, o un mapa de z0 elaborado a partir de INEGI Uso de Suelo y
  Vegetación serie VII).

Modelo de flujo (fidelidad "WAsP-lite"):

* Orografía: teoría lineal de flujo potencial (capa externa de Jackson-Hunt)
  resuelta con FFT sobre la malla, por sector de dirección y a altura de buje.
  Es la misma familia de modelos que el BZ de WAsP, sin la capa interna ni la
  malla polar. Válida en terreno moderado (RIX < ~5 %); se reporta RIX.
* Rugosidad: z0 efectivo aguas arriba por sector (promedio logarítmico con
  peso exponencial por distancia) y corrección logarítmica respecto a una
  altura de mezcla.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
from pyproj import CRS, Transformer
from scipy.ndimage import map_coordinates

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
if os.path.exists("/root/.ccr/ca-bundle.crt"):  # entorno con proxy
    os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")

# Rugosidad por clase de ESA WorldCover [m] (valores típicos de la literatura
# de WAsP / Silva et al. 2007; ajustables por el usuario)
WORLDCOVER_Z0 = {
    10: 0.80,   # Cobertura arbórea
    20: 0.10,   # Matorral (típico en el norte de México)
    30: 0.03,   # Pastizal
    40: 0.05,   # Cultivos
    50: 1.00,   # Zona urbana
    60: 0.005,  # Suelo desnudo / vegetación escasa
    70: 0.001,  # Nieve y hielo
    80: 0.0002, # Agua
    90: 0.03,   # Humedal herbáceo
    95: 0.50,   # Manglar
    100: 0.01,  # Musgo y liquen
}
WORLDCOVER_NAMES = {10: "Árboles", 20: "Matorral", 30: "Pastizal", 40: "Cultivos", 50: "Urbano",
                    60: "Suelo desnudo", 70: "Nieve/hielo", 80: "Agua", 90: "Humedal", 95: "Manglar",
                    100: "Musgo/liquen"}


def utm_crs(lat: float, lon: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


@dataclass
class Grid:
    """Malla regular en UTM. Los arreglos tienen fila 0 al SUR (y creciente)."""

    crs: CRS
    x0: float  # centro de la celda [0, 0]
    y0: float
    dx: float
    nx: int
    ny: int
    elev: np.ndarray | None = None
    z0: np.ndarray | None = None
    landcover: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    @property
    def x(self):
        return self.x0 + self.dx * np.arange(self.nx)

    @property
    def y(self):
        return self.y0 + self.dx * np.arange(self.ny)

    @property
    def bounds(self):
        h = self.dx / 2
        return self.x0 - h, self.y0 - h, self.x0 + self.dx * (self.nx - 1) + h, self.y0 + self.dx * (self.ny - 1) + h

    def north_up_transform(self):
        from rasterio.transform import from_origin

        left, bottom, right, top = self.bounds
        return from_origin(left, top, self.dx, self.dx)

    def lonlat_bounds(self, pad_deg=0.01):
        t = Transformer.from_crs(self.crs, 4326, always_xy=True)
        l, b, r, tp = self.bounds
        xs = np.array([l, r, l, r, (l + r) / 2, (l + r) / 2, l, r])
        ys = np.array([b, b, tp, tp, b, tp, (b + tp) / 2, (b + tp) / 2])
        lon, lat = t.transform(xs, ys)
        return lon.min() - pad_deg, lat.min() - pad_deg, lon.max() + pad_deg, lat.max() + pad_deg

    def sample(self, arr, x, y, order=1, mode="nearest"):
        col = (np.asarray(x, float) - self.x0) / self.dx
        row = (np.asarray(y, float) - self.y0) / self.dx
        return map_coordinates(arr, [np.atleast_1d(row), np.atleast_1d(col)], order=order, mode=mode)


def make_grid(lats, lons, buffer_km=8.0, dx=100.0, crs: CRS | None = None) -> tuple[Grid, np.ndarray, np.ndarray]:
    """Malla que cubre el layout + un margen. Devuelve (grid, x_turbinas, y_turbinas)."""
    lats, lons = np.asarray(lats, float), np.asarray(lons, float)
    crs = crs or utm_crs(float(np.mean(lats)), float(np.mean(lons)))
    tr = Transformer.from_crs(4326, crs, always_xy=True)
    x, y = tr.transform(lons, lats)
    b = buffer_km * 1000
    x0 = math.floor((x.min() - b) / dx) * dx
    y0 = math.floor((y.min() - b) / dx) * dx
    nx = int(math.ceil((x.max() + b - x0) / dx)) + 1
    ny = int(math.ceil((y.max() + b - y0) / dx)) + 1
    return Grid(crs, x0, y0, dx, nx, ny), np.asarray(x), np.asarray(y)


# --------------------------------------------------------------------------
# Descargas
# --------------------------------------------------------------------------

def copernicus_tile_url(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    name = f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"
    return f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"


def worldcover_tile_url(lat_floor3: int, lon_floor3: int) -> str:
    ns = "N" if lat_floor3 >= 0 else "S"
    ew = "E" if lon_floor3 >= 0 else "W"
    return ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat_floor3):02d}{ew}{abs(lon_floor3):03d}_Map.tif")


def _tiles(bounds, step):
    lo0, la0, lo1, la1 = bounds
    for la in range(int(math.floor(la0 / step) * step), int(math.floor(la1 / step) * step) + 1, step):
        for lo in range(int(math.floor(lo0 / step) * step), int(math.floor(lo1 / step) * step) + 1, step):
            yield la, lo


def _warp_into(src_path, grid: Grid, resampling, dtype="float32", fill=np.nan) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    dst = np.full((grid.ny, grid.nx), fill, dtype=dtype)
    with rasterio.open(src_path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            dst_transform=grid.north_up_transform(),
            dst_crs=grid.crs,
            dst_nodata=fill,
            resampling=getattr(Resampling, resampling),
            num_threads=4,
        )
    return np.flipud(dst)


def download_dem(grid: Grid, progress=None) -> np.ndarray:
    """Copernicus DEM GLO-30 remuestreado a la malla. Mar/sin datos -> 0 m."""
    tiles = list(_tiles(grid.lonlat_bounds(), 1))
    out = np.full((grid.ny, grid.nx), np.nan, dtype="float32")
    for i, (la, lo) in enumerate(tiles):
        if progress:
            progress(i / len(tiles), f"Copernicus DEM: mosaico {la},{lo}")
        try:
            part = _warp_into("/vsicurl/" + copernicus_tile_url(la, lo), grid, "bilinear")
        except Exception:
            continue  # mosaico inexistente (océano)
        m = np.isfinite(part)
        out[m] = part[m]
    out = np.where(np.isfinite(out), out, 0.0)
    grid.elev = out
    grid.meta["dem_source"] = "Copernicus DEM GLO-30"
    return out


def download_worldcover(grid: Grid, progress=None, z0_table: dict | None = None) -> np.ndarray:
    """ESA WorldCover 2021 -> clase dominante por celda -> z0."""
    tiles = list(_tiles(grid.lonlat_bounds(), 3))
    lc = np.zeros((grid.ny, grid.nx), dtype="uint8")
    for i, (la, lo) in enumerate(tiles):
        if progress:
            progress(i / len(tiles), f"ESA WorldCover: mosaico {la},{lo}")
        try:
            part = _warp_into("/vsicurl/" + worldcover_tile_url(la, lo), grid, "mode", dtype="uint8", fill=0)
        except Exception:
            continue
        m = part > 0
        lc[m] = part[m]
    lc[lc == 0] = 80  # sin datos -> agua (océano)
    grid.landcover = lc
    grid.z0 = landcover_to_z0(lc, z0_table)
    grid.meta["z0_source"] = "ESA WorldCover 2021"
    return grid.z0


def landcover_to_z0(lc: np.ndarray, z0_table: dict | None = None) -> np.ndarray:
    tab = dict(WORLDCOVER_Z0)
    if z0_table:
        tab.update(z0_table)
    lut = np.full(256, 0.03, dtype="float32")
    for k, v in tab.items():
        lut[int(k)] = v
    return lut[lc]


def load_raster_to_grid(path_or_file, grid: Grid, kind="elev") -> np.ndarray:
    """Carga un GeoTIFF propio (cualquier CRS). ``kind`` = 'elev' o 'z0'."""
    arr = _warp_into(path_or_file, grid, "bilinear" if kind == "elev" else "average")
    if kind == "elev":
        arr = np.where(np.isfinite(arr), arr, np.nanmean(arr))
        grid.elev = arr
        grid.meta["dem_source"] = "GeoTIFF del usuario"
    else:
        arr = np.where(np.isfinite(arr) & (arr > 0), arr, 0.03)
        grid.z0 = arr
        grid.meta["z0_source"] = "GeoTIFF del usuario"
    return arr


def flat_terrain(grid: Grid, elevation=0.0, z0=0.03):
    grid.elev = np.full((grid.ny, grid.nx), elevation, dtype="float32")
    grid.z0 = np.full((grid.ny, grid.nx), z0, dtype="float32")
    grid.meta.update(dem_source="Plano (sin terreno)", z0_source=f"Uniforme z0={z0}")


# --------------------------------------------------------------------------
# Modelo orográfico lineal (FFT)
# --------------------------------------------------------------------------

def _tukey(n, alpha=0.25):
    from scipy.signal.windows import tukey

    return tukey(n, alpha)


def orographic_speedup_map(elev: np.ndarray, dx: float, wd_from_deg: float, height: float,
                           cutoff_wavelength=30000.0, taper=0.25, cap=(-0.5, 0.8)) -> np.ndarray:
    """Perturbación fraccional de velocidad du/U a ``height`` sobre el terreno
    para viento que viene de ``wd_from_deg``.

    Flujo potencial linealizado:  û = Ĥ · k_par² / |k| · exp(-|k| z)
    Las longitudes de onda mayores a ``cutoff_wavelength`` (escala que el
    reanálisis ya resuelve) se filtran.
    """
    ny, nx = elev.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    A = np.c_[xx.ravel(), yy.ravel(), np.ones(xx.size)]
    coef, *_ = np.linalg.lstsq(A, elev.ravel().astype(float), rcond=None)
    h = elev - (A @ coef).reshape(ny, nx)
    h = h * np.outer(_tukey(ny, taper), _tukey(nx, taper))
    py, px = 2 ** int(math.ceil(math.log2(2 * ny))), 2 ** int(math.ceil(math.log2(2 * nx)))
    hp = np.zeros((py, px))
    hp[:ny, :nx] = h
    H = np.fft.rfft2(hp)
    ky = 2 * np.pi * np.fft.fftfreq(py, dx)[:, None]
    kx = 2 * np.pi * np.fft.rfftfreq(px, dx)[None, :]
    K = np.hypot(kx, ky)
    th = math.radians(wd_from_deg)
    ex, ey = -math.sin(th), -math.cos(th)  # dirección hacia donde va el flujo
    kpar = kx * ex + ky * ey
    with np.errstate(divide="ignore", invalid="ignore"):
        F = np.where(K > 0, kpar**2 / K, 0.0) * np.exp(-K * height)
    if cutoff_wavelength:
        kc = 2 * np.pi / cutoff_wavelength
        F *= 1 - np.exp(-((K / kc) ** 2))
    du = np.fft.irfft2(F * H, s=(py, px))[:ny, :nx]
    return np.clip(du, *cap)


def terrain_speedups(grid: Grid, xs, ys, height, n_sectors=12, ref_xy=None, return_maps=False, **kw):
    """Factores (1 + du/U) por turbina y sector, normalizados respecto al punto
    de referencia (``ref_xy`` = posición de la torre; None = promedio de la
    malla, adecuado para reanálisis)."""
    if grid.elev is None:
        raise ValueError("Primero descargue o cargue el modelo de elevación.")
    fac = np.ones((len(xs), n_sectors))
    maps = []
    for s in range(n_sectors):
        du = orographic_speedup_map(grid.elev, grid.dx, s * 360 / n_sectors, height, **kw)
        f_t = 1 + grid.sample(du, xs, ys)
        f_r = 1 + (grid.sample(du, [ref_xy[0]], [ref_xy[1]])[0] if ref_xy is not None else 0.0)
        fac[:, s] = f_t / f_r
        if return_maps:
            maps.append(du)
    return (fac, maps) if return_maps else fac


# --------------------------------------------------------------------------
# Rugosidad
# --------------------------------------------------------------------------

def upwind_z0(grid: Grid, xs, ys, n_sectors=12, max_dist=10000.0, e_fold=2500.0, n_rays=5) -> np.ndarray:
    """z0 efectivo aguas arriba por turbina y sector [m] (promedio logarítmico,
    peso exp(-r/e_fold))."""
    if grid.z0 is None:
        raise ValueError("Primero descargue o cargue el mapa de rugosidad.")
    lnz = np.log(np.maximum(grid.z0, 1e-5))
    r = np.arange(grid.dx / 2, max_dist, grid.dx)
    w = np.exp(-r / e_fold)
    width = 360 / n_sectors
    out = np.zeros((len(xs), n_sectors))
    for s in range(n_sectors):
        acc = np.zeros(len(xs))
        for off in np.linspace(-width / 2, width / 2, n_rays + 2)[1:-1]:
            th = math.radians(s * width + off)
            px = np.asarray(xs)[:, None] + r[None, :] * math.sin(th)  # hacia de dónde viene el viento
            py = np.asarray(ys)[:, None] + r[None, :] * math.cos(th)
            vals = grid.sample(lnz, px.ravel(), py.ravel()).reshape(px.shape)
            acc += (vals * w).sum(1) / w.sum()
        out[:, s] = np.exp(acc / n_rays)
    return out


def roughness_factors(z0_local: np.ndarray, z0_ref, height: float, blending_height=300.0) -> np.ndarray:
    """U(z; z0_local)/U(z; z0_ref) suponiendo el mismo viento a la altura de
    mezcla: ln(z/z0)/ln(h_b/z0)."""
    def prof(z0):
        z0 = np.minimum(np.asarray(z0, float), height / 5)
        return np.log(height / z0) / np.log(blending_height / z0)

    return prof(z0_local) / prof(z0_ref)


def domain_z0(grid: Grid) -> float:
    return float(np.exp(np.mean(np.log(np.maximum(grid.z0, 1e-5)))))


# --------------------------------------------------------------------------
# Complejidad del terreno
# --------------------------------------------------------------------------

def rix(grid: Grid, xs, ys, radius=3500.0, critical_slope=0.3, n_radials=72) -> np.ndarray:
    """Ruggedness Index (%): fracción de los radios con pendiente > 30 %.
    RIX > 5 % indica que los modelos lineales sobrestiman la aceleración."""
    r = np.arange(0, radius + grid.dx, grid.dx)
    out = np.zeros(len(xs))
    for i, (x0, y0) in enumerate(zip(xs, ys)):
        th = np.radians(np.arange(n_radials) * 360 / n_radials)
        px = x0 + r[None, :] * np.sin(th)[:, None]
        py = y0 + r[None, :] * np.cos(th)[:, None]
        z = grid.sample(grid.elev, px.ravel(), py.ravel()).reshape(px.shape)
        slope = np.abs(np.diff(z, axis=1)) / grid.dx
        out[i] = 100 * np.mean(slope > critical_slope)
    return out
