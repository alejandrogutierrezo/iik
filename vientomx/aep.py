"""Motor de cálculo de la producción anual de energía (AEP) de un parque eólico."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pyproj import Transformer

from . import climate, terrain, wake
from .turbines import Turbine

HOURS_PER_YEAR = 8766.0

DEFAULT_LOSSES = {  # % de pérdida (multiplicativas), valores típicos en tierra
    "Disponibilidad de turbinas": 3.0,
    "Disponibilidad de BOP/subestación": 0.5,
    "Eléctricas (colección y transformación)": 2.0,
    "Desempeño de la turbina": 1.0,
    "Ambientales (suciedad, degradación de palas)": 1.0,
    "Restricciones / curtailment": 0.0,
}

DEFAULT_UNCERTAINTY = {  # incertidumbres (1 sigma)
    "Datos de viento (fuente)": ("ws", 7.0),  # % velocidad: reanálisis sin medición ~6-10 %
    "Extrapolación vertical": ("ws", 2.0),
    "Modelo de flujo (terreno/rugosidad)": ("ws", 2.5),
    "Curva de potencia": ("energy", 3.0),
    "Modelo de estelas (fracción de la pérdida)": ("wake_frac", 25.0),
    "Otras pérdidas": ("energy", 1.5),
}


@dataclass
class SimConfig:
    hub_height: float = 100.0
    n_sectors: int = 12  # sectores para el modelo de flujo
    dir_bin: float = 10.0  # resolución de dirección para estelas [°]
    ws_step: float = 0.5
    ws_max: float = 35.0
    wake_model: str = "jensen"  # 'jensen' | 'bastankhah' | 'none'
    wake_k: float = 0.075
    ti: float = 0.10
    use_terrain: bool = True
    use_roughness: bool = True
    blending_height: float = 300.0
    roughness_efold: float = 2500.0
    cutoff_wavelength: float = 30000.0
    losses: dict = field(default_factory=lambda: dict(DEFAULT_LOSSES))
    uncertainty: dict = field(default_factory=lambda: dict(DEFAULT_UNCERTAINTY))
    air_density: float | None = None  # None = calcular de los datos


@dataclass
class Results:
    per_turbine: pd.DataFrame
    summary: dict
    hourly_farm_kw: pd.Series
    monthly_diurnal: pd.DataFrame
    annual: pd.DataFrame
    sector: pd.DataFrame
    uncertainty: pd.DataFrame
    losses: pd.DataFrame
    tables: dict  # tablas de potencia (para mapas)
    flow_factors: np.ndarray
    xs: np.ndarray
    ys: np.ndarray


def _circ_interp(factors_sec: np.ndarray, angles_deg) -> np.ndarray:
    """Interpola factores por sector (N, S) a ángulos arbitrarios -> (N, len(angles))."""
    N, S = factors_sec.shape
    width = 360 / S
    a = (np.asarray(angles_deg) % 360) / width
    i0 = np.floor(a).astype(int) % S
    i1 = (i0 + 1) % S
    w = a - np.floor(a)
    return factors_sec[:, i0] * (1 - w) + factors_sec[:, i1] * w


def flow_factors(grid: terrain.Grid | None, xs, ys, cfg: SimConfig, ref_xy=None):
    """Factores de velocidad libre por turbina y sector (terreno x rugosidad)."""
    N, S = len(xs), cfg.n_sectors
    f_ter = np.ones((N, S))
    f_rou = np.ones((N, S))
    z0_up = np.full((N, S), np.nan)
    if grid is not None and cfg.use_terrain and grid.elev is not None:
        f_ter = terrain.terrain_speedups(grid, xs, ys, cfg.hub_height, S, ref_xy=ref_xy,
                                         cutoff_wavelength=cfg.cutoff_wavelength)
    if grid is not None and cfg.use_roughness and grid.z0 is not None:
        z0_up = terrain.upwind_z0(grid, xs, ys, S, e_fold=cfg.roughness_efold)
        if ref_xy is None:
            z0_ref = terrain.domain_z0(grid)
        else:
            z0_ref = terrain.upwind_z0(grid, [ref_xy[0]], [ref_xy[1]], S, e_fold=cfg.roughness_efold)
        f_rou = terrain.roughness_factors(z0_up, z0_ref, cfg.hub_height, cfg.blending_height)
    return f_ter, f_rou, z0_up


def run_aep(layout: pd.DataFrame, ts_hub: pd.DataFrame, turbine: Turbine, cfg: SimConfig,
            grid: terrain.Grid | None = None, rho: float = 1.225, ref_latlon=None,
            progress=None) -> Results:
    """Calcula la AEP.

    ``ts_hub``: serie horaria en el punto de referencia (reanálisis o torre) a
    altura de buje, con columnas ``ws`` y ``wd``.
    ``ref_latlon``: posición de la torre si los datos son medidos; None si son
    de reanálisis (se toma el promedio de la malla como referencia).
    """
    lat, lon = layout["lat"].to_numpy(float), layout["lon"].to_numpy(float)
    if grid is not None:
        crs = grid.crs
    else:
        crs = terrain.utm_crs(float(lat.mean()), float(lon.mean()))
    tr = Transformer.from_crs(4326, crs, always_xy=True)
    xs, ys = map(np.asarray, tr.transform(lon, lat))
    ref_xy = tr.transform(ref_latlon[1], ref_latlon[0]) if ref_latlon is not None else None
    N = len(xs)
    if progress:
        progress(0.05, "Modelo de flujo: terreno y rugosidad…")
    f_ter, f_rou, z0_up = flow_factors(grid, xs, ys, cfg, ref_xy)
    fac = f_ter * f_rou

    # ---------- tablas de potencia por dirección y velocidad ----------
    nd = int(round(360 / cfg.dir_bin))
    d_centers = np.arange(nd) * 360 / nd
    U = np.arange(0, cfg.ws_max + cfg.ws_step / 2, cfg.ws_step)
    M = len(U)
    sub = np.array([-1 / 3, 0, 1 / 3]) * (360 / nd)
    P_free = np.zeros((nd, N, M))
    P_wake = np.zeros((nd, N, M))
    U_wake = np.zeros((nd, N, M))
    for d in range(nd):
        if progress and d % 4 == 0:
            progress(0.1 + 0.75 * d / nd, f"Estelas: dirección {d_centers[d]:.0f}°")
        for off in sub:
            ang = d_centers[d] + off
            f_dir = _circ_interp(fac, [ang])[:, 0]
            u_free = f_dir[:, None] * U[None, :]
            if cfg.wake_model == "none":
                u_eff = u_free
            else:
                u_eff = wake.farm_speeds(xs, ys, ang, u_free, turbine, cfg.wake_model, cfg.wake_k, cfg.ti)
            P_free[d] += turbine.power(u_free, rho) / len(sub)
            P_wake[d] += turbine.power(u_eff, rho) / len(sub)
            U_wake[d] += u_eff / len(sub)

    # ---------- histograma (dirección, velocidad) de la serie ----------
    if progress:
        progress(0.88, "Integrando la serie de tiempo…")
    ts = ts_hub.dropna(subset=["ws", "wd"])
    di = np.floor(((ts["wd"].to_numpy() + 180 / nd) % 360) / (360 / nd)).astype(int) % nd

    def lookup_weights(ws):
        pos = np.clip(ws / cfg.ws_step, 0, M - 1 - 1e-9)
        i0 = np.floor(pos).astype(int)
        w1 = pos - i0
        return i0, w1

    def histogram(ws):
        i0, w1 = lookup_weights(ws)
        H = np.zeros((nd, M))
        np.add.at(H, (di, i0), 1 - w1)
        np.add.at(H, (di, i0 + 1), w1)
        return H / len(ws)

    ws = ts["ws"].to_numpy()
    H = histogram(ws)
    mean_free = np.einsum("dm,dnm->n", H, P_free)  # kW medios por turbina
    mean_wake = np.einsum("dm,dnm->n", H, P_wake)

    # velocidades medias en cada turbina
    f_hourly = None
    u_free_mean = np.zeros(N)
    u_wake_mean = np.zeros(N)
    for d in range(nd):
        f_d = _circ_interp(fac, [d_centers[d]])[:, 0]
        u_free_mean += f_d * np.sum(H[d] * U)
        u_wake_mean += np.einsum("m,nm->n", H[d], U_wake[d])

    # serie horaria del parque (bruta con estelas)
    i0, w1 = lookup_weights(ws)
    Pfarm = P_wake.sum(1)  # (nd, M)
    Pfarm_free = P_free.sum(1)
    hourly = Pfarm[di, i0] * (1 - w1) + Pfarm[di, i0 + 1] * w1
    hourly_free = Pfarm_free[di, i0] * (1 - w1) + Pfarm_free[di, i0 + 1] * w1
    loss_factor = np.prod([1 - v / 100 for v in cfg.losses.values()])
    hourly_net = pd.Series(hourly * loss_factor, index=ts.index, name="P_neta_kW")

    # sensibilidad energía-velocidad (dE/E por dU/U)
    i0s, w1s = lookup_weights(ws * 1.01)
    hourly_s = Pfarm[di, i0s] * (1 - w1s) + Pfarm[di, i0s + 1] * w1s
    sens = (hourly_s.mean() / max(hourly.mean(), 1e-9) - 1) / 0.01

    cap_kw = turbine.rated_kw * N
    aep_free = mean_free * HOURS_PER_YEAR / 1e3  # MWh por turbina
    aep_wake = mean_wake * HOURS_PER_YEAR / 1e3
    aep_net_t = aep_wake * loss_factor
    gross = aep_free.sum()
    wake_loss = 1 - aep_wake.sum() / gross if gross > 0 else 0.0
    net = aep_net_t.sum()

    # elevación y RIX
    if grid is not None and grid.elev is not None:
        elev = grid.sample(grid.elev, xs, ys)
        rix = terrain.rix(grid, xs, ys)
    else:
        elev = np.full(N, np.nan)
        rix = np.zeros(N)

    # pesos por sector para factores medios
    S = cfg.n_sectors
    sec = climate.sector_index(ts["wd"], S)
    wsec = np.bincount(sec, weights=ws**3, minlength=S)
    wsec = wsec / wsec.sum()

    per = pd.DataFrame({
        "ID": layout["id"].to_numpy(),
        "Latitud": lat, "Longitud": lon, "Este UTM [m]": xs.round(1), "Norte UTM [m]": ys.round(1),
        "Elevación [m]": np.round(elev, 1),
        "Factor terreno": (f_ter @ wsec).round(4),
        "Factor rugosidad": (f_rou @ wsec).round(4),
        "U libre [m/s]": u_free_mean.round(2),
        "U con estelas [m/s]": u_wake_mean.round(2),
        "AEP bruta [MWh]": aep_free.round(1),
        "Pérdida por estelas [%]": (100 * (1 - aep_wake / np.maximum(aep_free, 1e-9))).round(2),
        "AEP neta P50 [MWh]": aep_net_t.round(1),
        "Factor de planta [%]": (100 * aep_net_t * 1e3 / (turbine.rated_kw * HOURS_PER_YEAR)).round(1),
        "RIX [%]": rix.round(1),
    })

    # ---------- incertidumbre ----------
    nyears = (ts.index[-1] - ts.index[0]).days / 365.25
    iav = climate.interannual_variability(ts)
    unc_rows = []
    for name, (kind, val) in cfg.uncertainty.items():
        if kind == "ws":
            e = val * sens
        elif kind == "wake_frac":
            e = val / 100 * wake_loss * 100
        else:
            e = val
        unc_rows.append({"Componente": name, "Tipo": kind, "Valor": val, "Incertidumbre en energía [%]": e})
    mean_rix = float(np.mean(rix))
    if mean_rix > 5:
        e = 0.5 * (mean_rix - 5) * sens / 2
        unc_rows.append({"Componente": "Terreno complejo (RIX > 5 %)", "Tipo": "ws", "Valor": 0.5 * (mean_rix - 5) / 2,
                         "Incertidumbre en energía [%]": e})
    lt = iav * 100 / math.sqrt(max(nyears, 1)) * sens
    unc_rows.append({"Componente": "Representatividad de largo plazo", "Tipo": "ws", "Valor": iav * 100 / math.sqrt(max(nyears, 1)),
                     "Incertidumbre en energía [%]": lt})
    unc = pd.DataFrame(unc_rows)
    sigma_hist = math.sqrt(float((unc["Incertidumbre en energía [%]"] ** 2).sum()))
    sigma_1y = math.sqrt(sigma_hist**2 + (iav * 100 * sens) ** 2)
    sigma_10y = math.sqrt(sigma_hist**2 + (iav * 100 * sens / math.sqrt(10)) ** 2)
    z90, z75, z99 = 1.2816, 0.6745, 2.3263

    summary = {
        "Turbinas": N,
        "Modelo de turbina": turbine.name,
        "Capacidad instalada [MW]": cap_kw / 1e3,
        "Altura de buje [m]": cfg.hub_height,
        "Densidad del aire [kg/m³]": rho,
        "Velocidad media en referencia (buje) [m/s]": float(ws.mean()),
        "Velocidad media libre en turbinas [m/s]": float(u_free_mean.mean()),
        "AEP bruta [GWh]": gross / 1e3,
        "Pérdida por estelas [%]": 100 * wake_loss,
        "Otras pérdidas [%]": 100 * (1 - loss_factor),
        "AEP neta P50 [GWh]": net / 1e3,
        "Factor de planta neto [%]": 100 * net * 1e3 / (cap_kw * HOURS_PER_YEAR),
        "Horas equivalentes [h]": net * 1e3 / cap_kw,
        "Sensibilidad energía/velocidad": sens,
        "Variabilidad interanual del viento [%]": 100 * iav,
        "Años de datos": nyears,
        "Incertidumbre total, 1 año [%]": sigma_1y,
        "Incertidumbre total, 10 años [%]": sigma_10y,
        "AEP P75 (10 años) [GWh]": net / 1e3 * (1 - z75 * sigma_10y / 100),
        "AEP P90 (1 año) [GWh]": net / 1e3 * (1 - z90 * sigma_1y / 100),
        "AEP P90 (10 años) [GWh]": net / 1e3 * (1 - z90 * sigma_10y / 100),
        "AEP P99 (10 años) [GWh]": net / 1e3 * (1 - z99 * sigma_10y / 100),
        "RIX medio [%]": mean_rix,
    }

    local = hourly_net.tz_convert("America/Mexico_City")
    md = local.groupby([local.index.month, local.index.hour]).mean().unstack() / cap_kw * 100
    md.index.name, md.columns.name = "Mes", "Hora local"
    yr = hourly_net.groupby(hourly_net.index.year)
    annual = pd.DataFrame({"Energía neta [GWh]": yr.sum() / 1e6, "Horas con datos": yr.count()})
    annual = annual[annual["Horas con datos"] >= 0.9 * 8760]
    annual["Energía neta [GWh]"] *= 8760 / annual["Horas con datos"]  # normaliza huecos
    annual["Desviación vs P50 [%]"] = 100 * (annual["Energía neta [GWh]"] / (net / 1e3) - 1)

    sec_rows = []
    width = 360 / S
    for s in range(S):
        m = sec == s
        sec_rows.append({
            "Sector [°]": s * width, "Frecuencia [%]": 100 * m.mean(),
            "Energía bruta [%]": 100 * hourly_free[m].sum() / max(hourly_free.sum(), 1e-9),
            "Pérdida por estelas [%]": (100 * (1 - hourly[m].sum() / hourly_free[m].sum())
                                        if hourly_free[m].sum() > 1e-3 * hourly_free.sum() else np.nan),
        })

    losses_df = pd.DataFrame([{"Concepto": "Estelas", "Pérdida [%]": 100 * wake_loss}] +
                             [{"Concepto": k, "Pérdida [%]": v} for k, v in cfg.losses.items()])
    if progress:
        progress(1.0, "Listo")
    return Results(
        per_turbine=per, summary=summary, hourly_farm_kw=hourly_net, monthly_diurnal=md, annual=annual,
        sector=pd.DataFrame(sec_rows), uncertainty=unc, losses=losses_df,
        tables={"U": U, "dirs": d_centers, "P_free": P_free, "P_wake": P_wake, "U_wake": U_wake, "fac": fac},
        flow_factors=fac, xs=xs, ys=ys,
    )


def wake_map(res: Results, grid: terrain.Grid | None, turbine: Turbine, cfg: SimConfig, wd: float, u_ref: float,
             dx=50.0, margin=1500.0):
    """Mapa de velocidad a altura de buje para un caso (dirección, velocidad)."""
    xs, ys = res.xs, res.ys
    x = np.arange(xs.min() - margin, xs.max() + margin, dx)
    y = np.arange(ys.min() - margin, ys.max() + margin, dx)
    X, Y = np.meshgrid(x, y)
    f_t = _circ_interp(res.flow_factors, [wd])[:, 0]
    u_free_t = f_t * u_ref
    if cfg.wake_model == "none":
        u_eff_t = u_free_t
    else:
        u_eff_t = wake.farm_speeds(xs, ys, wd, u_free_t[:, None], turbine, cfg.wake_model, cfg.wake_k, cfg.ti)[:, 0]
    U_bg = np.full_like(X, u_ref, dtype=float)
    if grid is not None and grid.elev is not None and cfg.use_terrain:
        du = terrain.orographic_speedup_map(grid.elev, grid.dx, wd, cfg.hub_height,
                                            cutoff_wavelength=cfg.cutoff_wavelength)
        U_bg = u_ref * (1 + grid.sample(du, X.ravel(), Y.ravel()).reshape(X.shape))
    model = cfg.wake_model if cfg.wake_model != "none" else "jensen"
    Uw = wake.flow_field(xs, ys, wd, u_free_t, u_eff_t, turbine, X, Y, U_bg, model, cfg.wake_k, cfg.ti) \
        if cfg.wake_model != "none" else U_bg
    return x, y, Uw
