"""Modelos de estela de ingeniería.

* Jensen / Park (N.O. Jensen 1983; Katic et al. 1986): sombrero de copa,
  expansión lineal ``k`` (0.075 en tierra, 0.04-0.05 en mar), superposición
  por suma de cuadrados. Es el modelo por defecto de WAsP clásico.
* Gaussiano de Bastankhah & Porté-Agel (2014), con ``k* = 0.38 TI + 0.004``
  (Niayifar & Porté-Agel 2016). Superposición por suma de cuadrados.

Ambos operan sobre la velocidad libre local de cada turbina (que ya incluye
los efectos de terreno y rugosidad) y se resuelven de aguas arriba hacia aguas
abajo para usar el Ct correcto de cada turbina.
"""
from __future__ import annotations

import math

import numpy as np

from .turbines import Turbine


def _overlap_fraction(d, r_w, r_r):
    """Área de traslape entre la estela (radio r_w) y el rotor (radio r_r)
    separados una distancia lateral d, como fracción del área del rotor."""
    d = np.asarray(d, float)
    r_w = np.asarray(r_w, float)
    out = np.zeros(np.broadcast(d, r_w).shape)
    d, r_w = np.broadcast_arrays(d, r_w)
    full = d + r_r <= r_w
    none = d >= r_w + r_r
    part = ~(full | none)
    out[full] = 1.0
    if part.any():
        dd, rw = d[part], r_w[part]
        rr = r_r
        a1 = np.clip((dd**2 + rr**2 - rw**2) / (2 * dd * rr), -1, 1)
        a2 = np.clip((dd**2 + rw**2 - rr**2) / (2 * dd * rw), -1, 1)
        area = rr**2 * np.arccos(a1) + rw**2 * np.arccos(a2) - 0.5 * np.sqrt(
            np.clip((-dd + rr + rw) * (dd + rr - rw) * (dd - rr + rw) * (dd + rr + rw), 0, None))
        out[part] = area / (np.pi * rr**2)
    return out


def jensen_deficit(ct, x, r, D, k=0.075):
    """Déficit fraccional promedio en el rotor aguas abajo."""
    R = D / 2
    xpos = x > 0.1 * D
    rw = R + k * np.maximum(x, 0)
    a = 1 - np.sqrt(1 - np.clip(ct, 0, 0.99))
    deficit = a / (1 + k * np.maximum(x, 0) / R) ** 2
    ov = _overlap_fraction(np.abs(r), rw, R)
    return np.where(xpos, deficit * ov, 0.0)


def bastankhah_deficit(ct, x, r, D, ti=0.10):
    ct = np.clip(ct, 0, 0.99)
    xpos = x > 0.1 * D
    beta = 0.5 * (1 + np.sqrt(1 - ct)) / np.sqrt(1 - ct)
    eps = 0.2 * np.sqrt(beta)
    kstar = 0.38 * ti + 0.004
    sigma_D = kstar * np.maximum(x, 0) / D + eps
    arg = np.clip(1 - ct / (8 * sigma_D**2), 0, None)
    C = 1 - np.sqrt(arg)
    # promedio sobre el rotor: aproximación con 7 puntos (centro + anillo)
    pts = np.array([0.0] + [0.6 * D / 2] * 6)
    ang = np.r_[0, np.linspace(0, 2 * np.pi, 7)[:-1]]
    acc = 0
    for p, a in zip(pts, ang):
        rr = np.hypot(r + p * np.cos(a), p * np.sin(a))
        acc = acc + np.exp(-(rr**2) / (2 * (sigma_D * D) ** 2))
    return np.where(xpos, C * acc / len(pts), 0.0)


def farm_speeds(xs, ys, wd_from_deg, u_free, turbine: Turbine, model="jensen", k=0.075, ti=0.10):
    """Velocidad efectiva en cada turbina.

    ``u_free``: arreglo (N, M) de velocidades libres locales para M casos
    (p. ej. M bins de velocidad). Devuelve (N, M)."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    u_free = np.atleast_2d(np.asarray(u_free, float))
    N = len(xs)
    th = math.radians(wd_from_deg)
    ex, ey = -math.sin(th), -math.cos(th)
    s = xs * ex + ys * ey  # coordenada a lo largo del flujo
    order = np.argsort(s)
    D = turbine.rotor_diameter
    u_eff = u_free.copy()
    ct_up = np.zeros_like(u_free)
    for idx, i in enumerate(order):
        if idx > 0:
            ups = order[:idx]
            dxw = s[i] - s[ups]
            dr = (xs[i] - xs[ups]) * ey - (ys[i] - ys[ups]) * ex
            mask = dxw > 0.1 * D
            if mask.any():
                ups, dxw, dr = ups[mask], dxw[mask], dr[mask]
                ctj = ct_up[ups]  # (n_up, M)
                if model == "jensen":
                    dfc = jensen_deficit(ctj, dxw[:, None], dr[:, None], D, k)
                else:
                    dfc = bastankhah_deficit(ctj, dxw[:, None], dr[:, None], D, ti)
                # déficit relativo a la velocidad de la turbina que genera la estela
                ratio = u_eff[ups] / np.maximum(u_free[i][None, :], 1e-6)
                tot = np.sqrt(np.sum((dfc * ratio) ** 2, axis=0))
                u_eff[i] = u_free[i] * np.clip(1 - tot, 0, 1)
        ct_up[i] = turbine.thrust_coefficient(u_eff[i])
    return u_eff


def flow_field(xs, ys, wd_from_deg, u_free_turb, u_eff_turb, turbine: Turbine, X, Y, U_bg,
               model="jensen", k=0.075, ti=0.10):
    """Mapa de velocidad (para visualización) con las estelas de todas las
    turbinas para un caso. ``U_bg`` es la velocidad libre en cada punto."""
    th = math.radians(wd_from_deg)
    ex, ey = -math.sin(th), -math.cos(th)
    D = turbine.rotor_diameter
    tot = np.zeros_like(X, dtype=float)
    for j in range(len(xs)):
        dxw = (X - xs[j]) * ex + (Y - ys[j]) * ey
        dr = (X - xs[j]) * ey - (Y - ys[j]) * ex
        ct = float(turbine.thrust_coefficient(u_eff_turb[j]))
        if model == "jensen":
            R = D / 2
            rw = R + k * np.maximum(dxw, 0)
            dfc = np.where((dxw > 0) & (np.abs(dr) <= rw), (1 - math.sqrt(1 - ct)) / (1 + k * np.maximum(dxw, 0) / R) ** 2, 0)
        else:
            beta = 0.5 * (1 + math.sqrt(1 - ct)) / math.sqrt(1 - ct)
            sig = (0.38 * ti + 0.004) * np.maximum(dxw, 0) / D + 0.2 * math.sqrt(beta)
            C = 1 - np.sqrt(np.clip(1 - ct / (8 * sig**2), 0, None))
            dfc = np.where(dxw > 0, C * np.exp(-(dr**2) / (2 * (sig * D) ** 2)), 0)
        tot += (dfc * u_eff_turb[j] / max(u_free_turb[j], 1e-6)) ** 2
    return U_bg * np.clip(1 - np.sqrt(tot), 0, 1)
