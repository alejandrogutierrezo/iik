"""Estadística del recurso eólico: cizalladura, extrapolación a altura de buje,
densidad del aire, Weibull, rosa de vientos, MCP de largo plazo."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .met import MetData

R_DRY = 287.05


def sector_index(wd, n_sectors=12) -> np.ndarray:
    width = 360.0 / n_sectors
    return (np.floor(((np.asarray(wd) + width / 2) % 360) / width)).astype(int)


def shear_table(met: MetData, low: float | None = None, high: float | None = None, n_sectors=12,
                min_ws=3.0) -> pd.DataFrame:
    """Exponente de cizalladura alfa por sector y hora local del día (estabilidad
    atmosférica). ``alfa = ln(U_high/U_low)/ln(h_high/h_low)`` con medias."""
    hs = met.heights
    if len(hs) < 2:
        raise ValueError("Se requieren al menos dos alturas para calcular la cizalladura.")
    low = low or hs[0]
    high = high or hs[-1]
    df = met.df
    ul, uh = df[f"ws_{low:g}"], df[f"ws_{high:g}"]
    ok = (ul > min_ws) & (uh > min_ws)
    sec = pd.Series(sector_index(df[f"wd_{high:g}"].fillna(0), n_sectors), index=df.index)
    hour = df.index.tz_convert("America/Mexico_City").hour
    g = pd.DataFrame({"ul": ul[ok], "uh": uh[ok], "sec": sec[ok], "hour": np.asarray(hour)[ok.to_numpy()]})
    m = g.groupby(["sec", "hour"])[["ul", "uh"]].mean()
    alpha = np.log(m["uh"] / m["ul"]) / np.log(high / low)
    tab = alpha.unstack("hour").reindex(index=range(n_sectors), columns=range(24))
    overall = np.log(g.uh.mean() / g.ul.mean()) / np.log(high / low)
    return tab.fillna(overall).clip(-0.1, 0.6)


def extrapolate_to_height(met: MetData, hub_height: float, n_sectors=12, method="power_sector_hour",
                          alpha_fixed: float | None = None) -> pd.DataFrame:
    """Serie horaria ``ws``/``wd`` a altura de buje.

    Usa la altura disponible más cercana y ley potencial con alfa por
    sector x hora (o un alfa fijo)."""
    hs = met.heights
    ref = min(hs, key=lambda h: abs(np.log(h / hub_height)))
    df = met.df
    ws_ref = df[f"ws_{ref:g}"]
    wd_col = f"wd_{ref:g}" if f"wd_{ref:g}" in df else [c for c in df.columns if c.startswith("wd_")][0]
    wd = df[wd_col]
    if abs(ref - hub_height) < 1e-6:
        alpha = pd.Series(0.0, index=df.index)
    elif alpha_fixed is not None or len(hs) < 2:
        alpha = pd.Series(alpha_fixed if alpha_fixed is not None else 1 / 7, index=df.index)
    else:
        tab = shear_table(met, n_sectors=n_sectors)
        sec = sector_index(wd.fillna(0), n_sectors)
        hour = df.index.tz_convert("America/Mexico_City").hour.to_numpy()
        alpha = pd.Series(tab.to_numpy()[sec, hour], index=df.index)
    out = pd.DataFrame({"ws": ws_ref * (hub_height / ref) ** alpha, "wd": wd}, index=df.index)
    out["alpha"] = alpha
    return out.dropna(subset=["ws", "wd"])


def air_density(met: MetData, site_elevation: float | None = None, hub_height: float = 100.0) -> float:
    """Densidad media del aire a altura de buje [kg/m3].

    Con T2m y presión superficial del reanálisis; se corrige por la diferencia
    entre la elevación del punto del reanálisis y la del sitio + buje
    (atmósfera hidrostática, escala ~8.4 km). Sin datos usa atmósfera estándar
    ISA, importante en el altiplano mexicano (p. ej. Zacatecas ~2,300 m:
    rho ~ 0.95 kg/m3)."""
    df = met.df
    z_site = (site_elevation if site_elevation is not None else (met.elevation or 0.0)) + hub_height
    if "t2m" in df and "ps" in df and df["ps"].notna().any():
        rho = (df["ps"] * 100) / (R_DRY * (df["t2m"] + 273.15))
        rho_ref = float(rho.mean())
        z_ref = (met.elevation or 0.0) + 2.0
        return rho_ref * np.exp(-(z_site - z_ref) / 8434.0)
    t0, p0, lapse = 288.15, 101325.0, 0.0065
    t = t0 - lapse * z_site
    p = p0 * (t / t0) ** 5.25588
    return p / (R_DRY * t)


def weibull_fit(ws) -> tuple[float, float]:
    """(A, k) por máxima verosimilitud."""
    ws = np.asarray(ws, float)
    ws = ws[np.isfinite(ws) & (ws > 0.1)]
    if ws.size < 10:
        return np.nan, np.nan
    k, _, a = stats.weibull_min.fit(ws, floc=0)
    return float(a), float(k)


def sector_stats(ts: pd.DataFrame, n_sectors=12) -> pd.DataFrame:
    sec = sector_index(ts["wd"], n_sectors)
    rows = []
    width = 360 / n_sectors
    for s in range(n_sectors):
        w = ts["ws"].to_numpy()[sec == s]
        a, k = weibull_fit(w)
        rows.append({"Sector": str(s), "Dirección [°]": s * width, "Frecuencia [%]": 100 * w.size / len(ts),
                     "Media [m/s]": w.mean() if w.size else np.nan, "Weibull A": a, "Weibull k": k,
                     "Densidad de potencia [W/m²]": 0.5 * 1.225 * np.mean(w**3) if w.size else np.nan})
    a, k = weibull_fit(ts["ws"])
    rows.append({"Sector": "Total", "Dirección [°]": np.nan, "Frecuencia [%]": 100.0, "Media [m/s]": ts["ws"].mean(),
                 "Weibull A": a, "Weibull k": k, "Densidad de potencia [W/m²]": 0.5 * 1.225 * np.mean(ts["ws"] ** 3)})
    return pd.DataFrame(rows)


def wind_rose_table(ts: pd.DataFrame, n_sectors=16, bins=(0, 3, 6, 9, 12, 15, 50)) -> pd.DataFrame:
    sec = sector_index(ts["wd"], n_sectors)
    cats = pd.cut(ts["ws"], bins=list(bins), right=False)
    tab = pd.crosstab(sec, cats, normalize=True) * 100
    tab.index = tab.index * 360 / n_sectors
    return tab.reindex(np.arange(n_sectors) * 360 / n_sectors, fill_value=0)


def annual_means(ts: pd.DataFrame, min_coverage=0.9) -> pd.Series:
    g = ts["ws"].groupby(ts.index.year)
    cov = g.count() / (24 * 365)
    return g.mean()[cov >= min_coverage]


def interannual_variability(ts: pd.DataFrame) -> float:
    """Coeficiente de variación de la velocidad media anual (típico 4-7 %)."""
    am = annual_means(ts)
    if len(am) < 3:
        return 0.06
    return float(am.std(ddof=1) / am.mean())


def calibrate_to_mean(ts: pd.DataFrame, target_mean: float) -> tuple[pd.DataFrame, float]:
    """Escala la serie para que su media coincida con un valor objetivo (p. ej.
    la media del Global Wind Atlas a la misma altura o una medición)."""
    f = target_mean / ts["ws"].mean()
    out = ts.copy()
    out["ws"] = out["ws"] * f
    return out, float(f)


def calibrate_series(met: MetData, ts_hub: pd.DataFrame, target_mean: float, target_height: float
                     ) -> tuple[pd.DataFrame, float]:
    """Escala la serie a buje para que la media **a ``target_height``** sea ``target_mean``.

    El factor se obtiene a la altura del valor objetivo (p. ej. GWA a 100 m) con
    la misma extrapolación vertical de la serie, y se aplica a la serie a buje."""
    ref = extrapolate_to_height(met, float(target_height))
    f = float(target_mean / ref["ws"].mean())
    out = ts_hub.copy()
    out["ws"] = out["ws"] * f
    return out, f


COARSE_SOURCES = ("ERA5", "NASA POWER", "MERRA")


def is_coarse_source(source: str) -> bool:
    """True si la fuente es un reanálisis global de malla gruesa (sin medición/MCP ni mesoescala)."""
    s = source or ""
    return any(k in s for k in COARSE_SOURCES) and "MCP" not in s and "Medición" not in s


def mcp_variance_ratio(measured: pd.DataFrame, reference: pd.DataFrame, by_sector=True,
                       n_sectors=12) -> tuple[pd.DataFrame, pd.DataFrame]:
    """MCP (Measure-Correlate-Predict) por razón de varianzas.

    ``measured`` y ``reference``: DataFrames con ``ws`` y ``wd`` horarios.
    Devuelve la serie de largo plazo sintética en el sitio de medición y una
    tabla con los parámetros por sector (pendiente, ordenada, R², N)."""
    conc = measured[["ws", "wd"]].join(reference[["ws", "wd"]], how="inner", lsuffix="_m", rsuffix="_r").dropna()
    if len(conc) < 24 * 90:
        raise ValueError("Se necesitan al menos ~3 meses de datos concurrentes para MCP.")
    # desviación (veer) media entre direcciones
    diff = np.radians(conc["wd_m"] - conc["wd_r"])
    veer = float(np.degrees(np.arctan2(np.sin(diff).mean(), np.cos(diff).mean())))
    sec_c = sector_index(conc["wd_r"], n_sectors) if by_sector else np.zeros(len(conc), int)
    sec_r = sector_index(reference["wd"], n_sectors) if by_sector else np.zeros(len(reference), int)
    pred = reference["ws"].copy()
    rows = []
    for s in np.unique(sec_r):
        m = sec_c == s
        if m.sum() < 50:  # pocos datos -> parámetros globales
            m = np.ones(len(conc), bool)
        x, y = conc["ws_r"].to_numpy()[m], conc["ws_m"].to_numpy()[m]
        slope = y.std() / x.std() if x.std() > 0 else 1.0
        offset = y.mean() - slope * x.mean()
        r2 = np.corrcoef(x, y)[0, 1] ** 2 if len(x) > 2 else np.nan
        sel = sec_r == s
        pred[sel] = (offset + slope * reference["ws"][sel]).clip(lower=0)
        rows.append({"Sector": int(s), "Pendiente": slope, "Ordenada": offset, "R²": r2, "N concurrentes": int(m.sum())})
    lt = pd.DataFrame({"ws": pred, "wd": (reference["wd"] + veer) % 360}, index=reference.index)
    info = pd.DataFrame(rows)
    info.attrs["veer"] = veer
    info.attrs["r2_total"] = float(np.corrcoef(conc["ws_r"], conc["ws_m"])[0, 1] ** 2)
    return lt, info
