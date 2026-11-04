"""Reporte PDF del estudio de recurso eólico.

Reúne en un solo documento todas las etapas del estudio (sitio y layout, datos de viento,
terreno y rugosidad, aerogenerador, simulación), los supuestos y advertencias de cada una,
y los resultados con tablas y gráficas. Usa ReportLab para la maquetación y Matplotlib
(backend sin pantalla) para las gráficas.
"""
from __future__ import annotations

import datetime as _dt
import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__ as _VERSION
from . import aep, climate, terrain
from .met import MetData
from .turbines import Turbine

# --------------------------------------------------------------------------- datos del estudio


@dataclass
class Study:
    """Todo lo que el reporte necesita saber del estudio. Solo `layout` es obligatorio;
    las secciones cuyos datos faltan se marcan como pendientes."""

    layout: pd.DataFrame
    met: MetData | None = None
    turbine: Turbine | None = None
    hub_height: float = 100.0
    grid: terrain.Grid | None = None
    cfg: aep.SimConfig | None = None
    results: aep.Results | None = None
    calib: float | None = None  # velocidad media objetivo [m/s]
    calib_h: float | None = None  # altura del objetivo [m]
    calib_ref: tuple | None = None  # (lat, lon) del punto de calibración
    gwa_info: dict | None = None
    mcp_info: pd.DataFrame | None = None
    ref_latlon: tuple | None = None
    title: str = "Evaluación de recurso eólico y producción anual"
    project: str = ""
    author: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- estilos

_C_PRIMARY = "#1f4e79"
_C_ACCENT = "#4c78a8"
_C_WARN_BG = "#fff4e5"
_C_WARN = "#b45309"
_C_NOTE_BG = "#eef4fb"
_C_GRID = "#d0d7de"


def _register_fonts():
    """Registra DejaVu Sans (incluida en Matplotlib) para tener acentos, σ, α, ≈, ³…"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import matplotlib

    if "DejaVu" in pdfmetrics.getRegisteredFontNames():
        return "DejaVu", "DejaVu-Bold"
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", str(d / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(d / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu",
                                      boldItalic="DejaVu-Bold")
        return "DejaVu", "DejaVu-Bold"
    except Exception:  # pragma: no cover - sin DejaVu, caer a Helvetica
        return "Helvetica", "Helvetica-Bold"


def _styles(font, bold):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    ss = getSampleStyleSheet()
    base = dict(fontName=font, fontSize=9.2, leading=12.5)
    st = {
        "body": ParagraphStyle("body", parent=ss["BodyText"], **base, spaceAfter=4),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontName=font, fontSize=7.8, leading=10,
                                textColor=colors.HexColor("#555555")),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=7.6, leading=9.4),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=7.6, leading=9.4, textColor=colors.white),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, spaceBefore=4, spaceAfter=8,
                             textColor=colors.HexColor(_C_PRIMARY)),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4, keepWithNext=1,
                             textColor=colors.HexColor(_C_PRIMARY)),
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, leading=27, spaceAfter=10,
                                textColor=colors.HexColor(_C_PRIMARY)),
        "subtitle": ParagraphStyle("subtitle", fontName=font, fontSize=12, leading=16, spaceAfter=4,
                                   textColor=colors.HexColor("#333333")),
        "bullet": ParagraphStyle("bullet", parent=ss["BodyText"], **base, leftIndent=10, bulletIndent=2),
        "kpi_v": ParagraphStyle("kpi_v", fontName=bold, fontSize=14, leading=17, alignment=1,
                                textColor=colors.HexColor(_C_PRIMARY)),
        "kpi_l": ParagraphStyle("kpi_l", fontName=font, fontSize=7.5, leading=9, alignment=1,
                                textColor=colors.HexColor("#555555")),
    }
    return st


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v, nd=2) -> str:
    if v is None:
        return "—"
    if isinstance(v, (bool, np.bool_)):
        return "sí" if v else "no"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return "—"
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.{nd}f}"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


# --------------------------------------------------------------------------- gráficas (matplotlib)

def _mpl():
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9,
                         "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e5e7eb", "grid.linewidth": 0.6,
                         "axes.axisbelow": True, "legend.fontsize": 7, "legend.frameon": False})
    return plt


def _fig_to_image(fig, width_cm: float):
    from reportlab.lib.units import cm
    from reportlab.platypus import Image

    plt = _mpl()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_cm * cm
    img.drawHeight = width_cm * cm * ratio
    return img


def _utm_xy(lat, lon):
    from pyproj import Transformer
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    tr = Transformer.from_crs(4326, terrain.utm_crs(float(lat.mean()), float(lon.mean())), always_xy=True)
    x, y = tr.transform(lon, lat)
    return np.asarray(x), np.asarray(y)


def _chart_layout(study: Study):
    plt = _mpl()
    lay = study.layout
    x, y = _utm_xy(lay.lat, lay.lon)
    x, y = (x - x.mean()) / 1e3, (y - y.mean()) / 1e3
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    ax.scatter(x, y, s=28, color=_C_ACCENT, edgecolor="black", linewidth=0.5, zorder=3)
    if len(lay) <= 60:
        for xi, yi, t in zip(x, y, lay["id"].astype(str)):
            ax.annotate(t, (xi, yi), xytext=(3, 3), textcoords="offset points", fontsize=5.5, color="#333")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("Este relativo al centro [km]")
    ax.set_ylabel("Norte relativo al centro [km]")
    ax.set_title("Layout del parque (proyección UTM)")
    return fig


def _chart_wind(ts: pd.DataFrame):
    """Rosa de vientos, histograma con Weibull, ciclos anual y diario, medias anuales."""
    from scipy import stats

    plt = _mpl()
    fig = plt.figure(figsize=(7.4, 6.6))
    gs = fig.add_gridspec(2, 2, hspace=0.42, wspace=0.28)

    # rosa
    ax = fig.add_subplot(gs[0, 0], projection="polar")
    rose = climate.wind_rose_table(ts)
    theta = np.deg2rad(np.asarray(rose.index, float))
    width = 2 * np.pi / len(rose) * 0.9
    bottom = np.zeros(len(rose))
    cmap = plt.get_cmap("viridis")
    for i, col in enumerate(rose.columns):
        vals = rose[col].to_numpy(float)
        ax.bar(theta, vals, width=width, bottom=bottom, color=cmap(i / max(len(rose.columns) - 1, 1)),
               edgecolor="white", linewidth=0.3, label=f"{col.left:g}–{col.right:g}")
        bottom += vals
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_title("Rosa de vientos (% del tiempo)", pad=14)
    ax.tick_params(labelsize=6)
    ax.legend(title="m/s", loc="upper left", bbox_to_anchor=(1.12, 1.05), fontsize=6, title_fontsize=6)

    # distribución
    ax = fig.add_subplot(gs[0, 1])
    A, k = climate.weibull_fit(ts.ws)
    ws = ts.ws.dropna().to_numpy(float)
    bins = np.arange(0, max(25.0, np.ceil(np.nanpercentile(ws, 99.9)) + 1), 0.5)
    ax.hist(ws, bins=bins, density=True, color=_C_ACCENT, alpha=0.85, label="Datos")
    xx = np.linspace(0, bins[-1], 300)
    ax.plot(xx, stats.weibull_min.pdf(xx, k, scale=A), color="#e45756", lw=2, label=f"Weibull A={A:.2f}, k={k:.2f}")
    ax.set_xlabel("Velocidad [m/s]")
    ax.set_ylabel("Densidad de probabilidad")
    ax.set_title("Distribución de velocidades")
    ax.legend()

    local = ts.ws.tz_convert("America/Mexico_City")
    ax = fig.add_subplot(gs[1, 0])
    mo = local.groupby(local.index.month).mean()
    ax.bar(mo.index, mo.values, color=_C_ACCENT)
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(list("EFMAMJJASOND"))
    ax.set_ylabel("m/s")
    ax.set_title("Ciclo anual (media mensual)")
    ax2 = fig.add_subplot(gs[1, 1])
    hr = local.groupby(local.index.hour).mean()
    ax2.plot(hr.index, hr.values, marker="o", ms=3, color=_C_ACCENT)
    ax2.set_xticks(range(0, 24, 3))
    ax2.set_xlabel("Hora local")
    ax2.set_ylabel("m/s")
    ax2.set_title("Ciclo diario")
    return fig


def _chart_annual_means(ts):
    plt = _mpl()
    am = climate.annual_means(ts)
    fig, ax = plt.subplots(figsize=(6.5, 2.4))
    if len(am):
        ax.bar(am.index.astype(int), am.values, color=_C_ACCENT)
        ax.axhline(am.mean(), color="#e45756", ls="--", lw=1, label=f"media {am.mean():.2f} m/s")
        ax.set_ylim(am.min() * 0.9, am.max() * 1.05)
        ax.legend(loc="lower right")
    ax.set_ylabel("m/s")
    ax.set_title("Velocidad media anual (años con ≥ 90 % de datos)")
    return fig


def _chart_terrain(study: Study, xs, ys):
    plt = _mpl()
    g = study.grid
    xk, yk = (g.x - g.x0) / 1e3, (g.y - g.y0) / 1e3
    txk, tyk = (np.asarray(xs) - g.x0) / 1e3, (np.asarray(ys) - g.y0) / 1e3
    ext = [xk.min(), xk.max(), yk.min(), yk.max()]
    fig, axs = plt.subplots(1, 2, figsize=(7.6, 3.6))
    im = axs[0].imshow(g.elev, origin="lower", extent=ext, cmap="terrain")
    fig.colorbar(im, ax=axs[0], shrink=0.8, label="m s.n.m.")
    axs[0].set_title("Elevación")
    if g.z0 is not None:
        im = axs[1].imshow(np.log10(np.maximum(g.z0, 1e-4)), origin="lower", extent=ext, cmap="YlGn",
                           vmin=-4, vmax=0)
        cb = fig.colorbar(im, ax=axs[1], shrink=0.8, label="z0 [m]")
        cb.set_ticks([-4, -3, -2, -1, 0])
        cb.set_ticklabels(["0.0001", "0.001", "0.01", "0.1", "1"])
    axs[1].set_title("Rugosidad z0")
    for ax in axs:
        ax.scatter(txk, tyk, s=10, c="white", edgecolor="black", linewidth=0.6, zorder=3)
        ax.set_xlabel("km")
        ax.set_aspect("equal")
        ax.grid(False)
    axs[0].set_ylabel("km")
    fig.tight_layout()
    return fig


def _chart_power_curve(t: Turbine, rho: float | None):
    plt = _mpl()
    u = np.linspace(0, 30, 301)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.plot(u, t.power(u), lw=2, color=_C_ACCENT, label="P (ρ = 1.225 kg/m³)")
    if rho:
        ax.plot(u, t.power(u, rho), lw=1.5, ls="--", color="#2a9d8f", label=f"P (ρ sitio ≈ {rho:.3f} kg/m³)")
    ax.set_xlabel("Velocidad [m/s]")
    ax.set_ylabel("Potencia [kW]")
    ax2 = ax.twinx()
    ax2.plot(u, t.thrust_coefficient(u), color="#e45756", lw=1.2,
             label="Ct" + (" (estimado)" if t.ct_estimated else ""))
    ax2.set_ylim(0, 1.1)
    ax2.set_ylabel("Ct [-]")
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="center right")
    ax.set_title(f"Curva de potencia y empuje — {t.name}")
    return fig


def _chart_results_map(res: aep.Results):
    plt = _mpl()
    pt = res.per_turbine
    x, y = (res.xs - res.xs.mean()) / 1e3, (res.ys - res.ys.mean()) / 1e3
    fig, axs = plt.subplots(1, 2, figsize=(7.6, 3.4))
    for ax, col, cmap in [(axs[0], "AEP neta P50 [MWh]", "viridis"), (axs[1], "Pérdida por estelas [%]", "magma_r")]:
        sc = ax.scatter(x, y, c=pt[col], cmap=cmap, s=30, edgecolor="black", linewidth=0.4, zorder=3)
        fig.colorbar(sc, ax=ax, shrink=0.85)
        ax.set_title(col)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("km")
    axs[0].set_ylabel("km")
    fig.tight_layout()
    return fig


def _chart_wake_map(study: Study):
    plt = _mpl()
    res, cfg = study.results, study.cfg
    wd = float(res.sector.loc[res.sector["Frecuencia [%]"].idxmax(), "Sector [°]"])
    u_ref = 9.0
    x, y, Uw = aep.wake_map(res, study.grid, study.turbine, cfg, wd, u_ref, dx=max(50.0, study.grid.dx
                                                                                    if study.grid else 50.0))
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    im = ax.imshow(Uw, origin="lower", extent=[0, (x.max() - x.min()) / 1e3, 0, (y.max() - y.min()) / 1e3],
                   cmap="turbo", aspect="equal")
    fig.colorbar(im, ax=ax, shrink=0.85, label="m/s")
    ax.scatter((res.xs - x.min()) / 1e3, (res.ys - y.min()) / 1e3, s=10, c="white", edgecolor="black",
               linewidth=0.5)
    ax.set_xlabel("km")
    ax.set_ylabel("km")
    ax.grid(False)
    ax.set_title(f"Velocidad a buje con estelas: viento de {wd:.0f}° (dirección dominante) a {u_ref:g} m/s")
    return fig


def _chart_results_time(res: aep.Results):
    plt = _mpl()
    fig, axs = plt.subplots(1, 2, figsize=(7.6, 3.1), gridspec_kw=dict(width_ratios=[1.25, 1]))
    md = res.monthly_diurnal
    im = axs[0].imshow(md.to_numpy(float), aspect="auto", cmap="viridis", origin="upper",
                       extent=[-0.5, 23.5, 12.5, 0.5])
    fig.colorbar(im, ax=axs[0], shrink=0.9, label="FP %")
    axs[0].set_yticks(range(1, 13))
    axs[0].set_yticklabels(list("EFMAMJJASOND"))
    axs[0].set_xticks(range(0, 24, 3))
    axs[0].set_xlabel("Hora local")
    axs[0].set_title("Factor de planta neto 12×24")
    axs[0].grid(False)
    an = res.annual
    p50 = res.summary["AEP neta P50 [GWh]"]
    if len(an):
        axs[1].bar(an.index.astype(int), an["Energía neta [GWh]"], color=_C_ACCENT)
        axs[1].set_ylim(an["Energía neta [GWh]"].min() * 0.9, an["Energía neta [GWh]"].max() * 1.05)
    axs[1].axhline(p50, color="#e45756", ls="--", lw=1, label="P50")
    axs[1].legend(loc="lower right")
    axs[1].set_ylabel("GWh")
    axs[1].set_title("Producción neta por año")
    fig.tight_layout()
    return fig


def _chart_energy_rose_exceedance(res: aep.Results):
    from scipy.stats import norm

    plt = _mpl()
    fig = plt.figure(figsize=(7.6, 3.3))
    ax = fig.add_subplot(1, 2, 1, projection="polar")
    sec = res.sector
    th = np.deg2rad(sec["Sector [°]"].to_numpy(float))
    w = 2 * np.pi / len(sec) * 0.9
    ax.bar(th, sec["Frecuencia [%]"], width=w, color="#bcd3ea", label="Frecuencia")
    ax.bar(th, sec["Energía bruta [%]"], width=w * 0.55, color=_C_ACCENT, label="Energía")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.tick_params(labelsize=6)
    ax.set_title("Rosa de frecuencia y energía (%)", pad=12)
    ax.legend(loc="upper left", bbox_to_anchor=(0.95, 1.1), fontsize=6)
    ax = fig.add_subplot(1, 2, 2)
    s = res.summary
    p = np.linspace(1, 99, 99)
    for lab, key, ls in [("10 años", "Incertidumbre total, 10 años [%]", "-"),
                         ("1 año", "Incertidumbre total, 1 año [%]", "--")]:
        ax.plot(p, s["AEP neta P50 [GWh]"] * (1 + norm.ppf(1 - p / 100) * s[key] / 100), ls=ls, label=lab)
    for q in (50, 75, 90):
        ax.axvline(q, color="#9ca3af", lw=0.6, ls=":")
    ax.set_xlabel("Probabilidad de excedencia [%]")
    ax.set_ylabel("GWh/año")
    ax.set_title("Curva de excedencia")
    ax.legend()
    fig.tight_layout()
    return fig


def _chart_losses(res: aep.Results):
    plt = _mpl()
    s = res.summary
    gross = s["AEP bruta [GWh]"]
    steps = [("Bruta", gross)]
    e = gross
    for row in res.losses.itertuples():
        e_new = e * (1 - row[2] / 100)
        if row[2]:
            steps.append((row[1], e_new - e))
        e = e_new
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    cum = 0.0
    labels = []
    for i, (lab, v) in enumerate(steps):
        if i == 0:
            ax.bar(i, v, color=_C_ACCENT)
            cum = v
        else:
            ax.bar(i, v, bottom=cum, color="#e45756")
            cum += v
        labels.append(lab if len(lab) < 22 else lab[:20] + "…")
    ax.bar(len(steps), cum, color="#2a9d8f")
    labels.append("Neta P50")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=6.5)
    ax.set_ylim(cum * 0.8, gross * 1.03)
    ax.set_ylabel("GWh/año")
    ax.set_title("Cascada de energía: de bruta a neta")
    return fig


# --------------------------------------------------------------------------- bloques de texto/tablas

def _table(df: pd.DataFrame, st, col_widths=None, max_rows=None, nd=2, font_size=None):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    df = df.copy()
    note = None
    if max_rows and len(df) > max_rows:
        note = f"… {len(df) - max_rows} filas más (vea el reporte Excel)."
        df = df.head(max_rows)
    cell = st["cell"]
    data = [[Paragraph(_esc(c), st["cellb"]) for c in df.columns]]
    for row in df.itertuples(index=False):
        data.append([Paragraph(_esc(_fmt(v, nd)), cell) for v in row])
    t = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_C_PRIMARY)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor(_C_GRID)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    out = [t]
    if note:
        out.append(Paragraph(note, st["small"]))
    return out


def _kv(rows, st, widths=(6.0, 10.5)):
    from reportlab.lib.units import cm
    df = pd.DataFrame([(k, _fmt(v)) for k, v in rows if v is not None], columns=["Parámetro", "Valor"])
    return _table(df, st, col_widths=[widths[0] * cm, widths[1] * cm])


def _box(title: str, items: list[str], st, kind="note"):
    """Recuadro de supuestos (azul) o advertencias (naranja)."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table, TableStyle

    if not items:
        return []
    bg, fg = (_C_WARN_BG, _C_WARN) if kind == "warn" else (_C_NOTE_BG, _C_PRIMARY)
    icon = "⚠ " if kind == "warn" else ""
    head = Paragraph(f'<font color="{fg}"><b>{icon}{_esc(title)}</b></font>', st["body"])
    body = [Paragraph(_esc(i), st["bullet"], bulletText="•") for i in items]
    t = Table([[head]] + [[b] for b in body], colWidths=[16.6 * cm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.HexColor(fg)),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    from reportlab.platypus import Spacer
    return [Spacer(1, 4), t, Spacer(1, 6)]


def _kpis(pairs, st):
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table, TableStyle

    cells = [[Paragraph(_esc(v), st["kpi_v"]) for _, v in pairs], [Paragraph(_esc(k), st["kpi_l"]) for k, _ in pairs]]
    w = 16.6 / len(pairs)
    t = Table(cells, colWidths=[w * cm] * len(pairs))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f7fa")),
        ("LINEABOVE", (0, 0), (-1, 0), 2, colors.HexColor(_C_PRIMARY)),
        ("INNERGRID", (0, 0), (-1, -1), 0, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


# --------------------------------------------------------------------------- análisis por etapa

def _min_spacing_D(lay: pd.DataFrame, rotor_d: float | None):
    if len(lay) < 2:
        return None, None
    from scipy.spatial import cKDTree
    x, y = _utm_xy(lay.lat, lay.lon)
    xy = np.column_stack([x, y])
    d = cKDTree(xy).query(xy, k=2)[0][:, 1]
    return float(d.min()), (float(d.min() / rotor_d) if rotor_d else None)


def _hub_series(study: Study):
    """Serie a buje como la usa la simulación (extrapolada y, si aplica, calibrada)."""
    hub = float(study.cfg.hub_height if study.cfg else study.hub_height)
    ts = climate.extrapolate_to_height(study.met, hub)
    f = None
    if study.calib:
        ts, f = climate.calibrate_series(study.met, ts, study.calib, study.calib_h or hub)
    return ts, hub, f


def _section_layout(study: Study, st, story):
    from reportlab.platypus import Paragraph
    lay = study.layout
    story.append(Paragraph("1 · Sitio y layout", st["h1"]))
    lat_c, lon_c = float(lay.lat.mean()), float(lay.lon.mean())
    x, y = _utm_xy(lay.lat, lay.lon)
    D = study.turbine.rotor_diameter if study.turbine else None
    dmin, dminD = _min_spacing_D(lay, D)
    n_osm = int(lay["osm_id"].notna().sum()) if "osm_id" in lay else 0
    story += _kv([
        ("Número de aerogeneradores", len(lay)),
        ("Centro del parque (lat, lon)", f"{lat_c:.5f}, {lon_c:.5f}"),
        ("Extensión E–O × N–S", f"{(x.max() - x.min()) / 1e3:.2f} × {(y.max() - y.min()) / 1e3:.2f} km"),
        ("Proyección de cálculo", terrain.utm_crs(lat_c, lon_c).name),
        ("Separación mínima entre turbinas", None if dmin is None else
         f"{dmin:,.0f} m" + (f" ({dminD:.1f} D)" if dminD else "")),
        ("Turbinas tomadas de OpenStreetMap", n_osm if n_osm else None),
    ], st)
    story.append(_fig_to_image(_chart_layout(study), 13.5))
    supuestos = ["Coordenadas geográficas WGS84 (EPSG:4326); los cálculos se hacen en la zona UTM del centro del parque.",
                 "Todas las turbinas tienen el mismo modelo y la misma altura de buje "
                 f"({study.cfg.hub_height if study.cfg else study.hub_height:g} m)."]
    if n_osm:
        supuestos.append("Las posiciones tomadas de OpenStreetMap (ODbL) son aportes voluntarios: verifíquelas contra "
                         "el layout oficial del proyecto.")
    story += _box("Supuestos", supuestos, st)
    warns = []
    if dminD is not None and dminD < 2:
        warns.append(f"Separación mínima de {dminD:.1f} D (< 2 D): posibles turbinas duplicadas o demasiado juntas; "
                     "los modelos de estela no son válidos a tan corta distancia.")
    elif dminD is not None and dminD < 3:
        warns.append(f"Separación mínima de {dminD:.1f} D (< 3 D): espere pérdidas por estela y cargas elevadas.")
    if not (14 <= lat_c <= 33 and -118.5 <= lon_c <= -86.5):
        warns.append("El centro del parque queda fuera de México: revise las coordenadas.")
    story += _box("Advertencias", warns, st, "warn")
    cols = [c for c in ["id", "lat", "lon", "hub_height", "osm_id"] if c in lay]
    tdf = lay[cols].copy()
    tdf.columns = [{"id": "ID", "lat": "Latitud", "lon": "Longitud", "hub_height": "Buje OSM [m]",
                    "osm_id": "osm_id"}[c] for c in cols]
    tdf["Latitud"] = tdf["Latitud"].map(lambda v: f"{v:.5f}")
    tdf["Longitud"] = tdf["Longitud"].map(lambda v: f"{v:.5f}")
    story.append(Paragraph("Coordenadas de los aerogeneradores", st["h2"]))
    story += _table(tdf, st, max_rows=80)


def _section_wind(study: Study, st, story):
    from reportlab.platypus import Paragraph, PageBreak
    story.append(PageBreak())
    story.append(Paragraph("2 · Datos de viento", st["h1"]))
    m = study.met
    if m is None:
        story.append(Paragraph("Etapa pendiente: no se han cargado datos de viento.", st["body"]))
        return
    ts, hub, f = _hub_series(study)
    t0, t1 = m.df.index.min(), m.df.index.max()
    nyears = (t1 - t0).days / 365.25
    A, k = climate.weibull_fit(ts.ws)
    cov = 100 * ts.ws.notna().mean()
    iav = climate.interannual_variability(ts)
    rows = [
        ("Fuente", m.source),
        ("Punto de referencia (lat, lon)", f"{m.lat:.4f}, {m.lon:.4f}"),
        ("Elevación del punto de referencia", f"{m.elevation:.0f} m" if m.elevation is not None else None),
        ("Periodo", f"{t0:%Y-%m-%d} a {t1:%Y-%m-%d} ({nyears:.1f} años)"),
        ("Registros horarios", len(m.df)),
        ("Alturas disponibles", ", ".join(f"{h:g} m" for h in m.heights)),
        ("Cobertura de datos a buje", f"{cov:.1f} %"),
        ("Velocidad media a buje", f"{ts.ws.mean():.2f} m/s a {hub:g} m"),
        ("Weibull A / k", f"{A:.2f} m/s / {k:.2f}"),
        ("Densidad de potencia (ρ = 1.225)", f"{0.5 * 1.225 * np.mean(ts.ws ** 3):.0f} W/m²"),
        ("Variabilidad interanual", f"{100 * iav:.1f} %"),
        ("Calibración", f"{study.calib:.2f} m/s a {study.calib_h or hub:g} m (factor {f:.3f})"
         if study.calib else "no"),
    ]
    if study.gwa_info:
        gi = study.gwa_info
        tv = np.asarray(gi.get("turbines", []), float)
        rows.append(("Global Wind Atlas", f"{gi['height']} m: centro {gi['center']:.2f} m/s"
                     + (f"; turbinas {np.nanmin(tv):.2f}–{np.nanmax(tv):.2f} m/s" if tv.size and np.isfinite(tv).any()
                        else "")))
    if study.mcp_info is not None and hasattr(study.mcp_info, "attrs") and "r2_total" in study.mcp_info.attrs:
        rows.append(("MCP (razón de varianzas)", f"R² = {study.mcp_info.attrs['r2_total']:.2f}; "
                     f"giro medio {study.mcp_info.attrs.get('veer', 0):.1f}°"))
    story += _kv(rows, st)
    if m.notes:
        story += _box("Notas de la fuente", list(m.notes), st)
    supuestos = [
        "Extrapolación vertical con ley potencial U(z) = U(zr)·(z/zr)^α, con α calculado por sector de dirección y "
        "hora local a partir de las dos alturas de la fuente (captura la estabilidad típica día/noche).",
        "La serie se considera representativa del clima de largo plazo del sitio (sin tendencia).",
        "El punto de referencia representa el clima regional; el modelo de flujo calcula las diferencias entre "
        "turbinas respecto a ese punto" + (" (el centro del parque, por la calibración)." if study.calib else "."),
    ]
    if study.calib:
        supuestos.append("La calibración escala toda la serie por un factor único y conserva la forma de la "
                         "distribución, la rosa de vientos y los ciclos.")
    if "MCP" in m.source:
        supuestos.append("Ajuste de largo plazo MCP por razón de varianzas por sector contra el reanálisis.")
    story += _box("Supuestos", supuestos, st)
    warns = []
    if climate.is_coarse_source(m.source) and not study.calib:
        warns.append("Reanálisis de malla gruesa (25–50 km) sin calibrar: en sierras, cañones y pasos de montaña suele "
                     "subestimar el viento. Calibre con el Global Wind Atlas o una medición, o use el WIND Toolkit.")
    if nyears < 10 and "Medición" not in m.source and "MCP" not in m.source:
        warns.append(f"Solo {nyears:.1f} años de datos: se recomiendan ≥ 10 años para representar el largo plazo.")
    if m.source.startswith("Medición") and nyears < 3:
        warns.append(f"Medición de {nyears:.1f} años sin ajuste de largo plazo (MCP): el resultado depende del año "
                     "medido.")
    if cov < 90:
        warns.append(f"Cobertura de datos de {cov:.0f} %: huecos importantes en la serie.")
    if f is not None and abs(f - 1) > 0.15:
        warns.append(f"El factor de calibración ({f:.2f}) se aleja más de 15 % de 1: la fuente original difiere "
                     "mucho de la referencia; revise ambas.")
    if len(m.heights) < 2:
        warns.append("La fuente tiene una sola altura: la cizalladura no se puede calcular de los datos.")
    if "WIND Toolkit" in m.source and study.grid is not None and study.grid.elev is not None and m.elevation:
        xs, ys = _grid_xy(study)
        de = float(np.nanmean(study.grid.sample(study.grid.elev, xs, ys))) - m.elevation
        if abs(de) > 150:
            warns.append(f"Las turbinas están {de:+.0f} m respecto a la elevación del punto de malla del WIND "
                         "Toolkit: el punto puede no representar su exposición.")
    story += _box("Advertencias", warns, st, "warn")
    story.append(_fig_to_image(_chart_wind(ts), 16.5))
    story.append(_fig_to_image(_chart_annual_means(ts), 14.5))
    story.append(Paragraph("Estadística por sector a altura de buje", st["h2"]))
    story += _table(climate.sector_stats(ts).round(2), st)


def _grid_xy(study: Study):
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, study.grid.crs, always_xy=True)
    xs, ys = tr.transform(study.layout.lon.to_numpy(float), study.layout.lat.to_numpy(float))
    return np.asarray(xs), np.asarray(ys)


def _is_flat(g: terrain.Grid) -> bool:
    return g.elev is not None and float(np.nanmax(g.elev) - np.nanmin(g.elev)) < 1e-6


def _section_terrain(study: Study, st, story):
    from reportlab.platypus import Paragraph, PageBreak
    story.append(PageBreak())
    story.append(Paragraph("3 · Terreno y rugosidad", st["h1"]))
    g = study.grid
    if g is None or g.elev is None:
        story.append(Paragraph("Etapa no realizada: el cálculo supone terreno plano y rugosidad uniforme.", st["body"]))
        story += _box("Advertencias", ["Sin modelo de terreno: no se consideran aceleraciones orográficas ni cambios "
                                       "de rugosidad entre turbinas."], st, "warn")
        return
    xs, ys = _grid_xy(study)
    flat = _is_flat(g)
    rx = terrain.rix(g, xs, ys) if not flat else np.zeros(len(xs))
    elev_t = g.sample(g.elev, xs, ys)
    story += _kv([
        ("Fuente de elevación", g.meta.get("dem_source", "plano" if flat else "—")),
        ("Fuente de rugosidad", g.meta.get("z0_source", "—")),
        ("Malla de cálculo", f"{g.nx} × {g.ny} celdas de {g.dx:g} m ({g.nx * g.dx / 1e3:.1f} × "
                             f"{g.ny * g.dx / 1e3:.1f} km)"),
        ("Sistema de coordenadas", g.crs.name),
        ("Elevación de las turbinas", f"{np.nanmin(elev_t):.0f}–{np.nanmax(elev_t):.0f} m s.n.m. "
                                      f"(media {np.nanmean(elev_t):.0f} m)"),
        ("RIX de las turbinas", f"{rx.min():.1f}–{rx.max():.1f} % (media {rx.mean():.1f} %)"),
        ("z0 medio del dominio", f"{terrain.domain_z0(g):.3f} m" if g.z0 is not None else None),
    ], st)
    if not flat:
        story.append(_fig_to_image(_chart_terrain(study, xs, ys), 16.5))
    supuestos = [
        "Aceleración orográfica con teoría lineal de flujo potencial (Jackson–Hunt), 12 sectores; se filtran ondas "
        "> 30 km porque ese relieve ya está en el clima de referencia.",
        "Efecto de rugosidad con perfil logarítmico y altura de mezcla; z0 aguas arriba promediado con decaimiento "
        "exponencial por sector.",
        "Atmósfera neutra en el modelo de flujo; no se modelan separación del flujo ni encauzamiento.",
    ]
    if g.landcover is not None:
        supuestos.append("Rugosidad derivada de la cobertura ESA WorldCover con la tabla z0 por clase usada en el estudio.")
    if flat:
        supuestos.append("Terreno plano con rugosidad uniforme (sin efectos de terreno).")
    story += _box("Supuestos", supuestos, st)
    warns = []
    if rx.max() > 5:
        n5 = int((rx > 5).sum())
        warns.append(f"{n5} turbina(s) con RIX > 5 % (máx. {rx.max():.1f} %): el modelo lineal tiende a sobrestimar la "
                     "aceleración en terreno escarpado. Se recomienda CFD o corrección RIX.")
    if flat:
        warns.append("Terreno plano: no se consideran efectos de orografía ni de rugosidad entre turbinas.")
    bx = min(xs.min() - g.x[0], g.x[-1] - xs.max(), ys.min() - g.y[0], g.y[-1] - ys.max())
    if bx < 3000:
        warns.append(f"El margen de la malla alrededor del parque es de solo {bx / 1e3:.1f} km: puede no incluir "
                     "colinas o cambios de rugosidad relevantes.")
    story += _box("Advertencias", warns, st, "warn")
    tdf = pd.DataFrame({"ID": study.layout["id"].astype(str), "Elevación [m]": np.round(elev_t, 0),
                        "RIX [%]": np.round(rx, 1)})
    if g.z0 is not None:
        tdf["z0 local [m]"] = np.round(g.sample(g.z0, xs, ys), 3)
    story.append(Paragraph("Terreno en cada aerogenerador", st["h2"]))
    story += _table(tdf, st, max_rows=80)
    if g.landcover is not None:
        vals, cnt = np.unique(g.landcover[np.isfinite(g.landcover)] if g.landcover.dtype.kind == "f"
                              else g.landcover, return_counts=True)
        lc = pd.DataFrame({"Cobertura": [terrain.WORLDCOVER_NAMES.get(int(v), str(v)) for v in vals],
                           "z0 [m]": [terrain.WORLDCOVER_Z0.get(int(v), np.nan) for v in vals],
                           "% del dominio": np.round(100 * cnt / cnt.sum(), 1)})
        story.append(Paragraph("Cobertura de suelo en el dominio", st["h2"]))
        story += _table(lc.sort_values("% del dominio", ascending=False), st)


def _section_turbine(study: Study, st, story):
    from reportlab.platypus import Paragraph, PageBreak
    story.append(PageBreak())
    story.append(Paragraph("4 · Aerogenerador", st["h1"]))
    t = study.turbine
    if t is None:
        story.append(Paragraph("Etapa pendiente: no se ha seleccionado un aerogenerador.", st["body"]))
        return
    hub = float(study.cfg.hub_height if study.cfg else study.hub_height)
    rho = study.results.summary.get("Densidad del aire [kg/m³]") if study.results else None
    story += _kv([
        ("Modelo", t.name),
        ("Potencia nominal", f"{t.rated_kw:,.0f} kW"),
        ("Diámetro de rotor", f"{t.rotor_diameter:g} m"),
        ("Potencia específica", f"{t.rated_kw * 1e3 / (np.pi * t.rotor_diameter ** 2 / 4):.0f} W/m²"),
        ("Velocidad de arranque / corte", f"{t.cut_in:g} / {t.cut_out:g} m/s"),
        ("Altura de buje del estudio", f"{hub:g} m"),
        ("Alturas de buje del fabricante", ", ".join(f"{h:g}" for h in t.hub_heights) + " m" if t.hub_heights else None),
        ("Fuente de la curva", t.source or "—"),
        ("Curva Ct", "estimada (disco actuador)" if t.ct_estimated else "del fabricante / referencia"),
        ("Densidad del aire del sitio", f"{rho:.3f} kg/m³" if rho else None),
    ], st)
    story.append(_fig_to_image(_chart_power_curve(t, rho), 14.5))
    story += _box("Supuestos", [
        "Curva de potencia a densidad estándar 1.225 kg/m³, corregida a la densidad del sitio con IEC 61400-12-1 "
        "(u·(ρ/1.225)^(1/3)), válida para turbinas reguladas por pitch.",
        "Sin pérdidas por histéresis de corte a alta velocidad ni por modos de operación reducida (ruido, cargas).",
    ], st)
    warns = []
    if t.ct_estimated:
        warns.append("La curva de empuje Ct se estimó (teoría de disco actuador); para estudios formales use la curva "
                     "Ct del fabricante.")
    if t.hub_heights and min(abs(hub - h) for h in t.hub_heights) > 1:
        warns.append(f"La altura de buje del estudio ({hub:g} m) no está entre las ofrecidas para este modelo.")
    if rho and rho < 1.1:
        warns.append(f"Densidad del aire baja ({rho:.3f} kg/m³): la producción baja notablemente frente a la curva "
                     "estándar.")
    story += _box("Advertencias", warns, st, "warn")


_WAKE_NAMES = {"jensen": "Jensen / Park (WAsP clásico)", "bastankhah": "Gaussiano Bastankhah", "none": "Sin estelas"}


def _section_sim(study: Study, st, story):
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, PageBreak
    story.append(PageBreak())
    story.append(Paragraph("5 · Configuración de la simulación", st["h1"]))
    c = study.cfg
    if c is None:
        story.append(Paragraph("Etapa pendiente: la simulación no se ha ejecutado.", st["body"]))
        return
    s = study.results.summary if study.results else {}
    story += _kv([
        ("Modelo de estelas", _WAKE_NAMES.get(c.wake_model, c.wake_model)),
        ("Constante de expansión k (Jensen)", c.wake_k if c.wake_model == "jensen" else None),
        ("Intensidad de turbulencia ambiente", c.ti if c.wake_model == "bastankhah" else None),
        ("Efectos de orografía", c.use_terrain),
        ("Efectos de rugosidad", c.use_roughness),
        ("Altura de mezcla para rugosidad", f"{c.blending_height:g} m"),
        ("Sectores del modelo de flujo", c.n_sectors),
        ("Resolución de dirección para estelas", f"{c.dir_bin:g}°"),
        ("Paso de velocidad", f"{c.ws_step:g} m/s"),
        ("Densidad del aire usada", f"{s['Densidad del aire [kg/m³]']:.3f} kg/m³"
         if "Densidad del aire [kg/m³]" in s else None),
    ], st)
    story.append(Paragraph("Pérdidas técnicas consideradas", st["h2"]))
    story += _table(pd.DataFrame({"Concepto": list(c.losses), "Pérdida [%]": list(c.losses.values())}), st,
                    col_widths=[10 * cm, 3 * cm])
    story.append(Paragraph("Incertidumbres (1σ)", st["h2"]))
    story += _table(pd.DataFrame({"Componente": list(c.uncertainty), "Tipo": [v[0] for v in c.uncertainty.values()],
                                  "Valor [%]": [v[1] for v in c.uncertainty.values()]}), st,
                    col_widths=[10 * cm, 2.5 * cm, 2.5 * cm])
    story.append(Paragraph("Tipo: «ws» = % de velocidad (convertido a energía con la sensibilidad calculada); "
                           "«energy» = % de energía; «wake_frac» = % de la pérdida por estelas.", st["small"]))
    supuestos = [
        "Pérdidas técnicas multiplicativas e independientes entre sí.",
        "Incertidumbres independientes y normales, combinadas en raíz de suma de cuadrados; la variabilidad "
        "interanual se reduce con √N para horizontes de N años.",
        "Superposición de estelas por suma cuadrática de déficits; las estelas no interactúan con el terreno.",
        "Cálculo por tablas de potencia por dirección y velocidad aplicado a cada hora de la serie.",
    ]
    story += _box("Supuestos", supuestos, st)
    warns = []
    if c.wake_model == "none":
        warns.append("Simulación sin estelas: la AEP está sobrestimada para cualquier parque de más de una turbina.")
    if c.wake_model == "jensen" and not (0.03 <= c.wake_k <= 0.1):
        warns.append(f"Constante de expansión k = {c.wake_k:g} fuera del rango usual (0.04–0.075).")
    if not c.use_terrain and study.grid is not None and study.grid.elev is not None and not _is_flat(study.grid):
        warns.append("Se desactivaron los efectos de orografía aunque hay un modelo de terreno.")
    if not c.use_roughness and study.grid is not None and study.grid.z0 is not None:
        warns.append("Se desactivaron los efectos de rugosidad aunque hay un mapa de rugosidad.")
    if sum(c.losses.values()) < 3:
        warns.append(f"Pérdidas técnicas totales de solo {sum(c.losses.values()):.1f} %: suelen estar entre 6 y 12 %.")
    unc_ws = c.uncertainty.get("Datos de viento (fuente)", ("ws", 0))[1]
    if study.met is not None and climate.is_coarse_source(study.met.source) and not study.calib and unc_ws < 6:
        warns.append(f"Incertidumbre de datos de viento de {unc_ws:g} % para un reanálisis sin calibrar: suele "
                     "ser 6–10 %.")
    story += _box("Advertencias", warns, st, "warn")


def _section_results(study: Study, st, story):
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, PageBreak
    story.append(PageBreak())
    story.append(Paragraph("6 · Resultados", st["h1"]))
    r = study.results
    if r is None:
        story.append(Paragraph("Sin resultados: ejecute la simulación para completar esta sección.", st["body"]))
        return
    s = r.summary
    story.append(_kpis([("Capacidad", f"{s['Capacidad instalada [MW]']:.1f} MW"),
                        ("AEP neta P50", f"{s['AEP neta P50 [GWh]']:.1f} GWh"),
                        ("AEP P90 (10 años)", f"{s['AEP P90 (10 años) [GWh]']:.1f} GWh"),
                        ("Factor de planta", f"{s['Factor de planta neto [%]']:.1f} %"),
                        ("Pérdida por estelas", f"{s['Pérdida por estelas [%]']:.1f} %")], st))
    warns = aep.reliability_warnings(s, study.met.source if study.met is not None else "", bool(study.calib))
    story += _box("Advertencias sobre la confiabilidad del resultado", warns, st, "warn")
    story.append(Paragraph("Resumen", st["h2"]))
    story += _table(pd.DataFrame(list(s.items()), columns=["Concepto", "Valor"]), st,
                    col_widths=[9.5 * cm, 6 * cm])
    story.append(_fig_to_image(_chart_results_map(r), 16.5))
    if study.turbine is not None and study.cfg is not None and len(r.xs) > 0:
        try:
            story.append(_fig_to_image(_chart_wake_map(study), 14.5))
        except Exception:  # el mapa de estelas es ilustrativo; no detiene el reporte
            pass
    story.append(_fig_to_image(_chart_results_time(r), 16.5))
    story.append(_fig_to_image(_chart_energy_rose_exceedance(r), 16.5))
    story.append(_fig_to_image(_chart_losses(r), 15))
    story.append(Paragraph("Pérdidas", st["h2"]))
    story += _table(r.losses.round(2), st, col_widths=[10 * cm, 3 * cm])
    story.append(Paragraph("Incertidumbre", st["h2"]))
    story += _table(r.uncertainty.round(2), st)
    story.append(Paragraph("Producción por sector de dirección", st["h2"]))
    story += _table(r.sector.round(2), st)
    if len(r.annual):
        story.append(Paragraph("Producción por año", st["h2"]))
        an = r.annual.reset_index().rename(columns={"index": "Año"}).round(2)
        an["Año"] = an["Año"].astype(str)
        story += _table(an, st)
    story.append(PageBreak())
    story.append(Paragraph("Resultados por aerogenerador", st["h2"]))
    cols = ["ID", "Elevación [m]", "U libre [m/s]", "U con estelas [m/s]", "AEP bruta [MWh]",
            "Pérdida por estelas [%]", "AEP neta P50 [MWh]", "Factor de planta [%]", "RIX [%]"]
    story += _table(r.per_turbine[[c for c in cols if c in r.per_turbine]], st, max_rows=150, nd=1)
    story += _box("Cómo leer los resultados", [
        "P50: producción neta anual que se espera superar con 50 % de probabilidad. P90 (10 años): valor que se "
        "supera con 90 % de probabilidad en promedio de 10 años; P90 (1 año) incluye la variabilidad de un año.",
        "El factor de planta 12×24 es el promedio por mes y hora local, útil para el análisis de mercado (CENACE).",
        "El mapa de estelas es un caso ilustrativo (dirección dominante, 9 m/s), no un promedio anual.",
    ], st)


def _section_method(st, story):
    from reportlab.platypus import Paragraph
    story.append(Paragraph("7 · Método y limitaciones", st["h1"]))
    for p in [
        "iik busca una fidelidad intermedia entre SAM y WAsP: modela cada aerogenerador con su terreno, rugosidad "
        "y estelas por dirección, pero con modelos lineales simplificados; no resuelve la estabilidad atmosférica ni "
        "la separación del flujo sobre colinas.",
        "Los resultados son adecuados para estudios de prefactibilidad y comparación de alternativas. Para "
        "financiamiento se requiere medición en sitio (torre o LiDAR) de al menos un año, ajuste de largo plazo y "
        "revisión por un consultor independiente.",
        "Fuentes de datos públicas: ERA5 (Copernicus/ECMWF), NASA POWER/MERRA-2, NREL WIND Toolkit, Global Wind "
        "Atlas (DTU), Copernicus DEM, ESA WorldCover, OpenStreetMap (ODbL), NREL turbine-models y OEDB.",
    ]:
        story.append(Paragraph(_esc(p), st["body"]))


# --------------------------------------------------------------------------- documento

def _cover(study: Study, st, story):
    from reportlab.platypus import Paragraph, Spacer
    lay = study.layout
    story.append(Spacer(1, 40))
    story.append(Paragraph(_esc(study.title), st["title"]))
    if study.project:
        story.append(Paragraph(_esc(study.project), st["subtitle"]))
    story.append(Paragraph(f"Centro del parque: {lay.lat.mean():.4f}, {lay.lon.mean():.4f} · {len(lay)} aerogeneradores"
                           + (f" · {_esc(study.turbine.name)}" if study.turbine else ""), st["subtitle"]))
    story.append(Paragraph(f"Fecha: {_dt.date.today():%d/%m/%Y}" + (f" · Elaboró: {_esc(study.author)}"
                                                                     if study.author else ""), st["subtitle"]))
    story.append(Spacer(1, 18))
    if study.results is not None:
        s = study.results.summary
        story.append(_kpis([("Capacidad", f"{s['Capacidad instalada [MW]']:.1f} MW"),
                            ("AEP neta P50", f"{s['AEP neta P50 [GWh]']:.1f} GWh/año"),
                            ("AEP P90 (10 años)", f"{s['AEP P90 (10 años) [GWh]']:.1f} GWh/año"),
                            ("Factor de planta neto", f"{s['Factor de planta neto [%]']:.1f} %")], st))
        story.append(Spacer(1, 14))
    story.append(Paragraph("Contenido", st["h2"]))
    status = [
        ("1 · Sitio y layout", True),
        ("2 · Datos de viento", study.met is not None),
        ("3 · Terreno y rugosidad", study.grid is not None and study.grid.elev is not None),
        ("4 · Aerogenerador", study.turbine is not None),
        ("5 · Configuración de la simulación", study.cfg is not None),
        ("6 · Resultados", study.results is not None),
        ("7 · Método y limitaciones", True),
    ]
    for name, done in status:
        story.append(Paragraph(_esc(name + ("" if done else "  (pendiente)")), st["bullet"], bulletText="•"))
    if study.notes:
        story.append(Paragraph("Notas del analista", st["h2"]))
        story.append(Paragraph(_esc(study.notes).replace("\n", "<br/>"), st["body"]))
    story.append(Spacer(1, 18))
    story.append(Paragraph("Cada etapa incluye su configuración, los supuestos del método y las advertencias detectadas "
                           "automáticamente. Las tablas completas están también en el reporte Excel.", st["small"]))


def build_pdf(study: Study) -> bytes:
    """Genera el reporte PDF y devuelve sus bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, SimpleDocTemplate

    font, bold = _register_fonts()
    st = _styles(font, bold)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=2.3 * cm, rightMargin=2.3 * cm, topMargin=2.0 * cm,
                            bottomMargin=1.8 * cm, title=study.title, author=study.author or "iik",
                            subject="Wind Resource Assessment", creator=f"iik {_VERSION}")
    head = study.project or study.title

    def deco(canvas, d):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(d.leftMargin, letter[1] - 1.25 * cm, f"iik · {head}"[:110])
        canvas.drawRightString(letter[0] - d.rightMargin, 1.0 * cm, f"Página {d.page}")
        canvas.drawString(d.leftMargin, 1.0 * cm, f"Generado el {_dt.datetime.now():%d/%m/%Y %H:%M}")
        canvas.setStrokeColor(colors.HexColor(_C_GRID))
        canvas.line(d.leftMargin, letter[1] - 1.4 * cm, letter[0] - d.rightMargin, letter[1] - 1.4 * cm)
        canvas.restoreState()

    story = []
    _cover(study, st, story)
    story.append(PageBreak())
    _section_layout(study, st, story)
    _section_wind(study, st, story)
    _section_terrain(study, st, story)
    _section_turbine(study, st, story)
    _section_sim(study, st, story)
    _section_results(study, st, story)
    story.append(PageBreak())
    _section_method(st, story)
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()
