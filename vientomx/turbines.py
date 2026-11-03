"""Librería de aerogeneradores: curvas de potencia y de empuje (Ct).

Fuentes que se cargan en tiempo de ejecución (no se redistribuyen datos):

* ``turbine-models`` (NREL / NatLabRockies, licencia BSD-3): turbinas de
  referencia y algunos modelos comerciales, muchas con curva Ct.
* ``windpowerlib`` (base de datos OEDB de la Open Energy Platform): ~70
  modelos comerciales con curva de potencia (Vestas, Siemens, Nordex, Enercon,
  GE, Senvion, ...). No traen Ct, así que se estima a partir del Cp
  (teoría de disco actuador).
* Archivos CSV del usuario (ficha técnica del fabricante).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

RHO_STD = 1.225  # kg/m3, densidad de referencia de las curvas de potencia


@dataclass
class Turbine:
    name: str
    rated_kw: float
    rotor_diameter: float
    ws: np.ndarray  # m/s
    power_kw: np.ndarray  # kW a densidad 1.225 kg/m3
    ct: np.ndarray | None = None
    source: str = ""
    ct_estimated: bool = False
    hub_heights: list[float] = field(default_factory=list)

    def __post_init__(self):
        self.ws = np.asarray(self.ws, dtype=float)
        self.power_kw = np.clip(np.asarray(self.power_kw, dtype=float), 0, None)
        order = np.argsort(self.ws)
        self.ws, self.power_kw = self.ws[order], self.power_kw[order]
        if self.ct is not None:
            self.ct = np.asarray(self.ct, dtype=float)[order]
        # Algunas curvas publicadas terminan antes del corte (p.ej. en 16-20 m/s
        # ya a potencia nominal): se extienden a potencia nominal hasta 25 m/s.
        if self.ws[-1] < 25 and self.power_kw[-1] >= 0.9 * self.power_kw.max():
            self.ws = np.r_[self.ws, 25.0]
            self.power_kw = np.r_[self.power_kw, self.power_kw[-1]]
            if self.ct is not None:
                self.ct = np.r_[self.ct, self.ct[-1]]
        if self.ct is None:
            self.ct = estimate_ct(self.ws, self.power_kw, self.rotor_diameter)
            self.ct_estimated = True
        if not self.rated_kw:
            self.rated_kw = float(self.power_kw.max())

    @property
    def cut_in(self) -> float:
        on = self.ws[self.power_kw > 0]
        return float(on.min()) if on.size else 0.0

    @property
    def cut_out(self) -> float:
        on = self.ws[self.power_kw > 0]
        return float(on.max()) if on.size else 0.0

    def power(self, u, rho: float = RHO_STD) -> np.ndarray:
        """Potencia [kW] para velocidad(es) u. Corrección de densidad tipo
        IEC 61400-12-1 para turbinas reguladas por pitch: u_n = u (rho/1.225)^(1/3)."""
        u = np.asarray(u, dtype=float) * (rho / RHO_STD) ** (1.0 / 3.0)
        p = np.interp(u, self.ws, self.power_kw, left=0.0, right=0.0)
        return np.where(u > self.cut_out + 1e-9, 0.0, p)

    def thrust_coefficient(self, u) -> np.ndarray:
        u = np.asarray(u, dtype=float)
        ct = np.interp(u, self.ws, self.ct, left=self.ct[0], right=self.ct[-1])
        return np.clip(ct, 0.0, 0.99)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"ws": self.ws, "power_kw": self.power_kw, "ct": self.ct})


def estimate_ct(ws, power_kw, rotor_diameter, rho=RHO_STD, eta=0.94) -> np.ndarray:
    """Estima Ct a partir de la curva de potencia (disco actuador).

    Cp_aero = P / (eta * 0.5 rho A U^3);  Cp = 4a(1-a)^2  ->  a  ->  Ct = 4a(1-a).
    Por debajo del arranque o arriba del corte se usa un Ct bajo (rotor en espera).
    """
    ws = np.asarray(ws, float)
    p = np.asarray(power_kw, float) * 1e3
    area = np.pi * rotor_diameter**2 / 4.0
    with np.errstate(divide="ignore", invalid="ignore"):
        cp = np.where(ws > 0, p / (eta * 0.5 * rho * area * ws**3), 0.0)
    cp = np.clip(np.nan_to_num(cp), 0.0, 16 / 27 - 1e-6)
    a_grid = np.linspace(0, 1 / 3, 2001)
    cp_grid = 4 * a_grid * (1 - a_grid) ** 2
    a = np.interp(cp, cp_grid, a_grid)
    ct_disk = 4 * a * (1 - a)
    # Las turbinas reales tienen Ct mayor al del disco ideal (pérdidas de punta,
    # arrastre). Se escala para que en el punto de Cp máximo Ct = 0.8, y en
    # región II (antes del Cp máximo) se mantiene ~0.8, lo que reproduce bien
    # las curvas Ct de referencia (NREL 5MW, IEA 3.4/10/15MW, V82).
    ct = ct_disk * (0.8 / max(ct_disk.max(), 1e-6))
    i_max = int(np.argmax(cp))
    region2 = (np.arange(ws.size) <= i_max) & (p > 0)
    ct = np.where(region2, np.maximum(ct, 0.8), ct)
    ct = np.where(p > 0, np.clip(ct, 0.05, 0.95), 0.05)
    return ct


# --------------------------------------------------------------------------
# Cargadores de librerías
# --------------------------------------------------------------------------

def _turbine_models_dir() -> Path | None:
    try:
        import turbine_models  # noqa: F401

        return Path(os.path.dirname(turbine_models.__file__)) / "data"
    except Exception:
        return None


def _windpowerlib_dir() -> Path | None:
    try:
        import windpowerlib  # noqa: F401

        return Path(os.path.dirname(windpowerlib.__file__)) / "oedb"
    except Exception:
        return None


_NREL_RE = re.compile(r"^(?P<name>.+)_(?P<mw>[\d.]+)(?P<unit>MW|kW)_(?P<d>[\d.]+)$")


def _load_nrel_csv(path: Path) -> Turbine | None:
    m = _NREL_RE.match(path.stem)
    if not m:
        return None
    df = pd.read_csv(path)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    if "Power [kW]" not in df.columns:
        return None  # curvas normalizadas
    rated = float(m["mw"]) * (1000 if m["unit"] == "MW" else 1)
    ct = df["Ct [-]"].to_numpy() if "Ct [-]" in df.columns else None
    ws = df["Wind Speed [m/s]"].to_numpy(float)
    p = df["Power [kW]"].to_numpy(float)
    # agrega puntos de cero antes de arranque si faltan
    if ws.min() > 0:
        ws = np.r_[0.0, ws.min() - 0.01, ws]
        p = np.r_[0.0, 0.0, p]
        if ct is not None:
            ct = np.r_[0.05, 0.05, ct]
    name = m["name"].replace("_", " ")
    return Turbine(
        name=f"{name} {rated/1000:g} MW D{float(m['d']):g}",
        rated_kw=rated,
        rotor_diameter=float(m["d"]),
        ws=ws,
        power_kw=p,
        ct=ct,
        source=f"NREL turbine-models ({path.parent.name})",
    )


def load_nrel_library(categories=("Onshore", "Offshore", "Distributed")) -> dict[str, Turbine]:
    base = _turbine_models_dir()
    out: dict[str, Turbine] = {}
    if base is None:
        return out
    for cat in categories:
        for f in sorted((base / cat).glob("*.csv")):
            try:
                t = _load_nrel_csv(f)
            except Exception:
                t = None
            if t is not None:
                out[t.name] = t
    return out


def load_oedb_library() -> dict[str, Turbine]:
    base = _windpowerlib_dir()
    out: dict[str, Turbine] = {}
    if base is None or not (base / "power_curves.csv").exists():
        return out
    pc = pd.read_csv(base / "power_curves.csv").set_index("turbine_type")
    meta = pd.read_csv(base / "turbine_data.csv").set_index("turbine_type")
    for ttype, row in pc.iterrows():
        row = row.dropna()
        if row.empty or ttype not in meta.index:
            continue
        info = meta.loc[ttype]
        if isinstance(info, pd.DataFrame):
            info = info.iloc[0]
        ws = row.index.astype(float).to_numpy()
        p_kw = row.to_numpy(float) / 1e3
        d = float(info["rotor_diameter"])
        hh = []
        if isinstance(info.get("hub_height"), str):
            for tok in re.split(r"[;,/]", info["hub_height"]):
                try:
                    hh.append(float(tok.strip()))
                except ValueError:
                    pass
        name = f"{info['manufacturer']} {info['name']} ({ttype})"
        try:
            out[name] = Turbine(
                name=name,
                rated_kw=float(info["nominal_power"]) / 1e3,
                rotor_diameter=d,
                ws=ws,
                power_kw=p_kw,
                ct=None,
                source="OEDB / windpowerlib (Ct estimado)",
                hub_heights=hh,
            )
        except Exception:
            continue
    return out


def load_turbine_csv(path_or_buffer, name: str, rotor_diameter: float, rated_kw: float | None = None) -> Turbine:
    """CSV del usuario con columnas de velocidad, potencia [kW] y opcionalmente Ct.

    Se aceptan nombres en español o inglés: ws/velocidad/wind speed,
    power/potencia (kW), ct/empuje.
    """
    df = pd.read_csv(path_or_buffer)
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(*keys):
        for k in keys:
            for lc, c in cols.items():
                if lc.startswith(k) or k in lc:
                    return c
        return None

    c_ws = pick("ws", "velocidad", "wind speed", "speed", "u")
    c_p = pick("power", "potencia", "p [", "kw")
    c_ct = pick("ct", "empuje", "thrust coef")
    if c_ws is None or c_p is None:
        raise ValueError("El CSV debe tener columnas de velocidad (m/s) y potencia (kW).")
    ct = df[c_ct].to_numpy(float) if c_ct else None
    return Turbine(
        name=name,
        rated_kw=rated_kw or float(df[c_p].max()),
        rotor_diameter=rotor_diameter,
        ws=df[c_ws].to_numpy(float),
        power_kw=df[c_p].to_numpy(float),
        ct=ct,
        source="CSV del usuario",
    )


def load_library() -> dict[str, Turbine]:
    """Librería combinada (NREL + OEDB)."""
    lib = {}
    lib.update(load_nrel_library())
    lib.update(load_oedb_library())
    return dict(sorted(lib.items(), key=lambda kv: kv[0].lower()))


def library_table(lib: dict[str, Turbine]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Modelo": t.name,
                "Potencia [kW]": round(t.rated_kw),
                "Diámetro [m]": t.rotor_diameter,
                "Potencia específica [W/m²]": round(t.rated_kw * 1e3 / (np.pi * t.rotor_diameter**2 / 4)),
                "Ct": "estimado" if t.ct_estimated else "fabricante/ref.",
                "Fuente": t.source,
            }
            for t in lib.values()
        ]
    )
