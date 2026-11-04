"""Estilos de la interfaz de iik: «Clásica» (Streamlit por defecto) y la variante «Nothing».

La variante Nothing sigue el sistema de diseño Nothing (nothing-design): monocromo, tipografía Space Grotesk /
Space Mono / Doto, etiquetas de instrumento en MAYÚSCULAS, sin sombras ni degradados, y el rojo #D71921 solo
como señal. Tiene modo oscuro (negro OLED) y claro (papel #F5F5F5).

Uso en la app::

    ui = tema.aplicar(st.session_state)     # al inicio del script, antes de dibujar nada
    ui.chart(fig)                           # en lugar de st.plotly_chart(fig)
    st.button(ui.L("🔎 Buscar"))            # sin emoji en la variante Nothing

El tema de Streamlit (colores, fuentes, tablas) se cambia en tiempo de ejecución con ``streamlit.config``. Esa
configuración es del proceso: si dos pestañas del navegador usan estilos distintos, cada una se vuelve a dibujar una
vez al interactuar. Para una app local de un solo usuario no tiene efecto práctico.
"""
from __future__ import annotations

import base64
import html
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st
from streamlit import config as _config

ESTILOS = {
    "clasica": "Clásica",
    "nothing-oscuro": "Nothing · oscuro",
    "nothing-claro": "Nothing · claro",
}
DEFAULT = "nothing-oscuro"  # estilo con el que abre la app
CLASICA = "clasica"

ACCENT = "#D71921"
SUCCESS = "#4A9E5C"
WARNING = "#D4A843"

_FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Doto:wght@400..900"
              "&family=Space+Grotesk:wght@300;400;500;700&family=Space+Mono:wght@400;700&display=swap")
_GROTESK = "'Space Grotesk':https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;700&display=swap"
_MONO = "'Space Mono':https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap"

# Tokens del sistema (tabla Dark / Light de nothing-design)
TOKENS = {
    True: dict(bg="#000000", surface="#111111", raised="#1A1A1A", border="#222222", border2="#333333",
               text3="#666666", text2="#999999", text1="#E8E8E8", text0="#FFFFFF", link="#5B9BF6"),
    False: dict(bg="#F5F5F5", surface="#FFFFFF", raised="#F0F0F0", border="#E8E8E8", border2="#CCCCCC",
                text3="#999999", text2="#666666", text1="#1A1A1A", text0="#000000", link="#007AFF"),
}


def _streamlit_theme(dark: bool) -> dict:
    t = TOKENS[dark]
    return {
        "base": "dark" if dark else "light",
        "primaryColor": t["text0"],
        "backgroundColor": t["bg"],
        "secondaryBackgroundColor": t["surface"],
        "textColor": t["text1"],
        "linkColor": t["link"],
        "borderColor": t["border2"],
        "dataframeBorderColor": t["border"],
        "dataframeHeaderBackgroundColor": t["bg"],
        "showWidgetBorder": True,
        "showSidebarBorder": True,
        "font": _GROTESK + ", sans-serif",
        "headingFont": _GROTESK + ", sans-serif",
        "codeFont": _MONO + ", monospace",
        "baseRadius": "8px",
        "buttonRadius": "full",
        "headingFontSizes": ["2.25rem", "1.5rem", "1.125rem"],
        "headingFontWeights": [500, 500, 500],
        "metricValueFontSize": "2rem",
        "redColor": ACCENT,
        "greenColor": SUCCESS,
        "yellowColor": WARNING,
        "orangeColor": WARNING,
        "blueColor": t["text2"],
        "grayColor": t["text2"],
        "chartCategoricalColors": [t["text0"], t["text2"], t["text3"], ACCENT, SUCCESS, WARNING],
    }


_THEME_KEYS = sorted(set(_streamlit_theme(True)) | set(_streamlit_theme(False)))


@st.cache_resource
def _original_theme() -> dict:
    """Valores de tema con los que arrancó el proceso (config.toml del usuario o los de Streamlit)."""
    out = {}
    for k in _THEME_KEYS:
        try:
            out[k] = _config.get_option(f"theme.{k}")
        except Exception:
            pass
    return out


def _set_streamlit_theme(values: dict) -> bool:
    """Ajusta el tema de Streamlit. Devuelve True si algo cambió (hace falta volver a dibujar)."""
    changed = False
    for k, v in values.items():
        try:
            if _config.get_option(f"theme.{k}") != v:
                _config.set_option(f"theme.{k}", v, "<estilo iik>")
                changed = True
        except Exception:  # opción que no existe en esta versión de Streamlit: se omite
            continue
    return changed


# ------------------------------------------------------------------ etiquetas
_CIRCLED = {c: f"{i:02d}" for i, c in enumerate("①②③④⑤⑥⑦⑧⑨", 1)}
_LEAD = re.compile(r"^[^\wÁÉÍÓÚÑáéíóúñ¿¡«(\[]+")


def strip_symbols(text: str) -> str:
    """Quita emoji y símbolos iniciales («🔎 Buscar» → «Buscar»; «① Sitio» → «01 Sitio»)."""
    for c, n in _CIRCLED.items():
        text = text.replace(c, n)
    return _LEAD.sub("", text).strip()


# ------------------------------------------------------------------ escalas de color
def _colorscales(dark: bool) -> dict:
    t = TOKENS[dark]
    lo, hi = t["surface"], t["text0"]
    return {
        "seq": [[0.0, lo], [0.5, t["text3"]], [1.0, hi]],
        # negativo = pérdida (rojo, la señal), cero = fondo, positivo = tinta
        "div": [[0.0, ACCENT], [0.5, t["bg"] if dark else t["surface"]], [1.0, hi]],
        # invertida: lo bajo resalta (p. ej. estelas como trazos de tinta sobre el flujo libre)
        "seq_r": [[0.0, hi], [0.5, t["text3"]], [1.0, lo]],
    }


def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha:.2f})"


def plotly_template(dark: bool):
    import plotly.graph_objects as go

    t = TOKENS[dark]
    mono = "'Space Mono', 'JetBrains Mono', monospace"
    axis = dict(gridcolor=t["border"], linecolor=t["border2"], zerolinecolor=t["border2"], zeroline=False,
                tickfont=dict(family=mono, size=10, color=t["text2"]), ticks="", showline=True,
                title=dict(font=dict(family=mono, size=10, color=t["text2"])))
    cs = _colorscales(dark)
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="'Space Grotesk', sans-serif", size=12, color=t["text1"]),
        title=dict(font=dict(family=mono, size=11, color=t["text2"]), x=0, xanchor="left"),
        colorway=[t["text0"], t["text2"], t["text3"], ACCENT, SUCCESS, WARNING],
        xaxis=dict(axis, showgrid=False), yaxis=dict(axis, showgrid=True),
        legend=dict(font=dict(family=mono, size=10, color=t["text2"]), bgcolor="rgba(0,0,0,0)", borderwidth=0),
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["border2"],
                        font=dict(family=mono, size=11, color=t["text1"])),
        polar=dict(bgcolor="rgba(0,0,0,0)",
                   angularaxis=dict(gridcolor=t["border"], linecolor=t["border2"],
                                    tickfont=dict(family=mono, size=10, color=t["text2"])),
                   radialaxis=dict(gridcolor=t["border"], linecolor=t["border2"], showline=False,
                                   tickfont=dict(family=mono, size=9, color=t["text3"]))),
        coloraxis=dict(colorscale=cs["seq"], colorbar=dict(outlinewidth=0, thickness=8,
                                                           tickfont=dict(family=mono, size=10, color=t["text2"]))),
        colorscale=dict(sequential=cs["seq"], diverging=cs["div"]),
        bargap=0.25,
        margin=dict(l=56, r=16, t=48, b=40),
    )
    cb = dict(outlinewidth=0, thickness=8, tickfont=dict(family=mono, size=10, color=t["text2"]),
              title=dict(font=dict(family=mono, size=10, color=t["text2"])))
    tpl.data.heatmap = [go.Heatmap(colorscale=cs["seq"], colorbar=cb)]
    tpl.data.bar = [go.Bar(marker=dict(color=t["text0"], line=dict(width=0)))]
    tpl.data.scatter = [go.Scatter(line=dict(width=1.5), marker=dict(size=5))]
    tpl.data.barpolar = [go.Barpolar(marker=dict(line=dict(width=0)))]
    return tpl


# ------------------------------------------------------------------ objeto de estilo
@dataclass
class Estilo:
    key: str = CLASICA
    nothing: bool = False
    dark: bool = True
    t: dict = field(default_factory=dict)

    # ---- textos
    def L(self, text: str) -> str:
        """Etiqueta para botones, pestañas y opciones: sin emoji en la variante Nothing."""
        return strip_symbols(text) if self.nothing else text

    # ---- colores y gráficas
    def color(self, role: str, classic: str) -> str:
        """Color de un elemento de gráfica. Roles: 'ink' (dato principal), 'muted', 'accent'."""
        if not self.nothing:
            return classic
        return {"ink": self.t["text0"], "muted": self.t["text2"], "accent": ACCENT}[role]

    def cs(self, kind: str, classic):
        """Escala continua: 'seq' (secuencial), 'seq_r' (secuencial invertida) o 'div' (divergente)."""
        return _colorscales(self.dark)[kind] if self.nothing else classic

    def ramp(self, n: int, classic: list[str]) -> list[str]:
        """n colores ordenados (p. ej. clases de velocidad en la rosa de vientos): opacidad creciente."""
        if not self.nothing:
            return [classic[min(i * 2, len(classic) - 1)] for i in range(n)]
        return [_hex_rgba(self.t["text0"], 0.15 + 0.85 * i / max(n - 1, 1)) for i in range(n)]

    def turbine_marker(self, size: int = 8) -> dict:
        """Marcador de turbinas sobre mapas de calor."""
        if not self.nothing:
            return dict(color="white", size=size, line=dict(color="black", width=1.5))
        return dict(color=ACCENT, size=size - 1, line=dict(color=self.t["bg"], width=1))

    @property
    def map_style(self) -> str:
        if not self.nothing:
            return "open-street-map"
        return "carto-darkmatter" if self.dark else "carto-positron"

    def style_folium(self, m, satellite: bool = False):
        """Mapa base monocromo (mosaicos de OSM en escala de grises; invertidos en modo oscuro)."""
        if not self.nothing or satellite:
            return m
        import folium

        f = "grayscale(1) invert(1) brightness(.85) contrast(.9)" if self.dark else "grayscale(1) contrast(.95)"
        m.get_root().header.add_child(folium.Element(
            f"<style>.leaflet-tile-pane{{filter:{f}}}.leaflet-container{{background:{self.t['bg']}}}"
            ".leaflet-control-attribution,.leaflet-control-scale-line{font-family:'Space Mono',monospace;"
            "font-size:10px}</style>"))
        return m

    def folium_marker(self, kind: str) -> dict:
        """Estilo de CircleMarker: 'layout', 'selected' u 'osm' (turbina de OSM pendiente)."""
        if not self.nothing:
            return {"layout": dict(color="#1e3a8a", weight=2, fill_color="#3b82f6", fill_opacity=0.9),
                    "selected": dict(color="#facc15", weight=3, fill_color="#3b82f6", fill_opacity=0.9),
                    "osm": dict(color="#c2410c", weight=2, fill_color="#fb923c", fill_opacity=0.8)}[kind]
        ink, bg = self.t["text0"], self.t["bg"]
        return {"layout": dict(color=bg, weight=2, fill_color=ink, fill_opacity=1.0),
                "selected": dict(color=ACCENT, weight=3, fill_color=ink, fill_opacity=1.0),
                "osm": dict(color=ACCENT, weight=2, fill_color=ACCENT, fill_opacity=0.25)}[kind]

    def chart(self, fig, **kw):
        """st.plotly_chart con la plantilla del estilo activo."""
        if not self.nothing:
            return st.plotly_chart(fig, **kw)
        fig.layout.template = plotly_template(self.dark)  # reemplaza (no mezcla) la plantilla de plotly
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        if fig.layout.margin.t is None:
            fig.update_layout(margin=dict(l=56, r=16, t=48, b=40))
        _recolor_px_defaults(fig, self.t["text0"])
        title = fig.layout.title.text
        if title:
            fig.update_layout(title_text=title.upper())
        return st.plotly_chart(fig, theme=None, **kw)

    # ---- bloques propios de la variante Nothing
    def header(self, step: int | None, title: str, total: int = 6):
        if self.nothing and step:
            st.html(f'<div class="nd-label nd-step">Paso {step:02d} / {total:02d}</div>')
        st.header(title)

    def notify(self, msg: str):
        """Aviso breve: toast en la clásica; línea de estado en línea [OK] … en Nothing (sin popups)."""
        if not self.nothing:
            st.toast(msg, icon="💾")
            return
        err = msg.startswith("⚠")
        tag, cls = ("ERROR", "nd-status nd-err") if err else ("OK", "nd-status")
        st.html(f'<div class="{cls}">[{tag}] {html.escape(strip_symbols(msg))}</div>')

    def css(self) -> str:
        return _css(self.dark) if self.nothing else ""


def _recolor_px_defaults(fig, ink: str):
    """plotly.express fija en cada traza el primer color de la paleta por defecto: pasarlo a tinta."""
    import plotly.colors as pc
    import plotly.io as pio

    defaults = {c.lower() for c in pc.qualitative.Plotly + pc.qualitative.D3}
    if "streamlit" in pio.templates:  # Streamlit usa colores comodín (#000001, …) que su frontend sustituye
        defaults |= {c.lower() for c in (pio.templates["streamlit"].layout.colorway or ())}
    for tr in fig.data:
        marker = getattr(tr, "marker", None)
        if marker is not None and isinstance(marker.color, str) and marker.color.lower() in defaults:
            marker.color = ink
        line = getattr(tr, "line", None)
        if line is not None and isinstance(line.color, str) and line.color.lower() in defaults:
            line.color = ink


def seg_bar(frac: float, n: int = 20, color: str | None = None, height: int = 10) -> str:
    """Barra de progreso segmentada (bloques discretos, 2 px de separación)."""
    frac = max(0.0, min(float(frac), 1.0))
    k = int(round(frac * n))
    style = f' style="--c:{color}"' if color else ""
    cells = "".join('<i class="on"></i>' if i < k else "<i></i>" for i in range(n))
    return f'<div class="nd-seg" style="height:{height}px"><span class="nd-seg-in"{style}>{cells}</span></div>'


def stat_rows(rows) -> str:
    """Filas «ETIQUETA ……… valor unidad». rows = [(etiqueta, valor, unidad, color|None), …]."""
    out = []
    for label, value, unit, color in rows:
        st_ = f' style="color:{color}"' if color else ""
        out.append(f'<div class="nd-row"><span class="nd-label">{html.escape(label)}</span>'
                   f'<span class="nd-val"{st_}>{html.escape(value)}<span class="nd-unit">{html.escape(unit)}</span>'
                   f"</span></div>")
    return '<div class="nd-rows">' + "".join(out) + "</div>"


def hero(label: str, value: str, unit: str, note: str = "") -> str:
    """Cifra principal de la pantalla en Doto (una por pantalla)."""
    n = f'<div class="nd-note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="nd-hero"><div class="nd-label">{html.escape(label)}</div>'
            f'<div class="nd-display">{html.escape(value)}<span class="nd-unit">{html.escape(unit)}</span></div>'
            f"{n}</div>")


BRAND_DIR = Path(__file__).resolve().parents[1] / "brand"
APP_NAME = "iik"
SUBTITLE = "Recurso eólico · México"


def logo_html(dark: bool = True, width: int = 120) -> str:
    """Logotipo oficial como <img> (el sanitizador de st.html quita los <svg> en línea).

    Según brand/BRAND.md: se usan los SVG tal cual (iik-logo-on-dark.svg sobre fondo oscuro,
    iik-logo-on-light.svg sobre claro), sin deformar (viewBox 4:3) y con ancho mínimo de 96 px.
    """
    name = "iik-logo-on-dark.svg" if dark else "iik-logo-on-light.svg"
    try:
        data = base64.b64encode((BRAND_DIR / name).read_bytes()).decode("ascii")
    except OSError:  # sin la carpeta brand/: el nombre en texto
        return f'<span class="nd-logo-txt">{APP_NAME}</span>'
    w = max(int(width), 96)
    return (f'<img src="data:image/svg+xml;base64,{data}" width="{w}" height="{w * 3 // 4}" '
            f'alt="{APP_NAME}" style="display:block">')


def favicon() -> str:
    """Ruta del ícono de la app (brand/iik-icon-dark.svg) o un emoji si no existe."""
    p = BRAND_DIR / "iik-icon-dark.svg"
    return str(p) if p.exists() else "🌬️"


def brand_header(dark: bool = False) -> str:
    """Logotipo + subtítulo (para la barra lateral de la interfaz clásica)."""
    return (f'<div style="padding:0 0 8px 0">{logo_html(dark, 120)}</div>'
            f'<div style="font-size:.8rem;opacity:.7;letter-spacing:.04em">{SUBTITLE}</div>')


AUTHOR = "Dr. Alejandro Gutiérrez Ortiz"


def credit() -> str:
    """Crédito de autoría (pie de la barra lateral)."""
    return (f'<div class="nd-credit" style="font-size:11px;letter-spacing:.06em;opacity:.7;line-height:1.5">'
            f"Desarrollado por {html.escape(AUTHOR)}</div>")


def sidebar_brand(steps: list[tuple[str, bool]], dark: bool = True) -> str:
    """Encabezado de la barra lateral: logotipo, avance segmentado y lista de pasos."""
    done = sum(ok for _, ok in steps)
    items = "".join(f'<li class="{"on" if ok else ""}"><span>{i:02d}</span><span>{html.escape(name)}</span>'
                    f'<b>{"●" if ok else "○"}</b></li>' for i, (name, ok) in enumerate(steps, 1))
    return (
        f'<div class="nd-brand"><div class="nd-logo">{logo_html(dark, 120)}</div>'
        f'<div class="nd-label">{SUBTITLE}</div></div>'
        f'<div class="nd-progress"><div class="nd-row nd-row-tight"><span class="nd-label">Avance del estudio</span>'
        f'<span class="nd-val">{done}<span class="nd-unit">/{len(steps)}</span></span></div>'
        f"{seg_bar(done / len(steps), len(steps), height=8)}<ol class='nd-steps'>{items}</ol></div>"
    )


def steps_tabs_css(key: str, done: list[bool], unlocked: list[bool], nothing: bool, dark: bool = True) -> str:
    """CSS de las pestañas de pasos: bloqueadas (gris en la clásica, tachadas en Nothing) y la línea inferior
    como barra de progreso, pintada bajo cada paso terminado (tinta en Nothing, color de texto en la clásica).

    ``done[i]`` y ``unlocked[i]`` se refieren a la pestaña i (0 = primera). La pestaña activa se marca con el
    indicador de Streamlit (rojo), que queda por encima de la barra de progreso.
    """
    root = f".st-key-{key} [role='tablist']"
    gap = 24 if nothing else 16
    ink = TOKENS[dark]["text0"] if nothing else "rgba(49,51,63,.85)"
    css = [f"{root}{{gap:{gap}px}}",
           f"{root}>[role='tab']::after{{content:'';position:absolute;left:0;bottom:0;height:2px;"
           f"background:transparent;transition:background-color .3s cubic-bezier(0.25,0.1,0.25,1);z-index:0}}",
           f"{root} .react-aria-SelectionIndicator{{z-index:1}}"]
    last = max((i for i, d in enumerate(done) if d), default=-1)
    for i, d in enumerate(done):
        if d:
            css.append(f"{root}>[role='tab']:nth-child({i + 1})::after{{background:{ink};"
                       f"right:{0 if i == last else -gap}px}}")
    for i, u in enumerate(unlocked):
        if not u:
            sel = f"{root}>[role='tab']:nth-child({i + 1})"
            css.append(f"{sel}{{pointer-events:none;cursor:not-allowed}}")
            if nothing:
                css.append(f"{sel} p{{text-decoration:line-through;text-decoration-thickness:1px;"
                           f"opacity:.55}}")
            else:
                css.append(f"{sel}{{opacity:.35}}")
    if nothing:
        css.append(f"{root} .react-aria-SelectionIndicator{{background-color:{ACCENT}!important}}")
    return "<style>" + "\n".join(css) + "</style>"


def _css(dark: bool) -> str:
    t = TOKENS[dark]
    root = (f":root{{--nd-bg:{t['bg']};--nd-surface:{t['surface']};--nd-raised:{t['raised']};"
            f"--nd-border:{t['border']};--nd-border2:{t['border2']};--nd-text3:{t['text3']};"
            f"--nd-text2:{t['text2']};--nd-text1:{t['text1']};--nd-text0:{t['text0']};--nd-accent:{ACCENT};"
            f"--nd-seg-off:{t['border'] if dark else '#E0E0E0'};}}")
    return f"@import url('{_FONTS_CSS}');\n{root}\n{_CSS_STATIC}"


_CSS_STATIC = r"""
:root{--nd-mono:'Space Mono','JetBrains Mono','SF Mono',monospace;--nd-sans:'Space Grotesk','DM Sans',system-ui,sans-serif;
      --nd-dot:'Doto','Space Mono',monospace;--nd-ease:cubic-bezier(0.25,0.1,0.25,1);}
/* ---------- tipografía de instrumento */
.nd-label,[data-testid="stWidgetLabel"] p,[data-testid="stMetricLabel"] p{font-family:var(--nd-mono)!important;
  font-size:11px!important;letter-spacing:.08em;text-transform:uppercase;color:var(--nd-text2)!important;line-height:1.3}
[data-testid="stMetricValue"]{font-family:var(--nd-mono)!important;letter-spacing:-.02em;color:var(--nd-text0)}
h1,h2,h3{letter-spacing:-.01em}
.nd-step{margin-bottom:-8px;color:var(--nd-text3)!important}
/* ---------- pestañas: navegación de texto (selectores de Streamlit ≥1.50 y anteriores) */
[data-testid="stTabs"] [role="tablist"]{gap:24px}
[data-testid="stTabs"] [role="tab"]{background:transparent}
[data-testid="stTabs"] [role="tab"] p{font-family:var(--nd-mono);font-size:12px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--nd-text3);transition:color .2s var(--nd-ease)}
[data-testid="stTabs"] [role="tab"]:hover p{color:var(--nd-text1)}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p{color:var(--nd-text0)}
[data-testid="stTabs"] .react-aria-SelectionIndicator,[data-baseweb="tab-highlight"]{background-color:var(--nd-text0)!important}
[data-baseweb="tab-border"]{background-color:var(--nd-border)!important;height:1px!important}
/* ---------- botones: píldora, mono, mayúsculas */
[data-testid^="stBaseButton"]{font-family:var(--nd-mono);min-height:44px;padding:0 16px;box-shadow:none!important;
  transition:border-color .2s var(--nd-ease),color .2s var(--nd-ease),background-color .2s var(--nd-ease)}
[data-testid^="stBaseButton"] p{font-family:var(--nd-mono);font-size:12px;letter-spacing:.06em;text-transform:uppercase}
[data-testid="stBaseButton-primary"]{background:var(--nd-text0)!important;border:1px solid var(--nd-text0)!important;
  color:var(--nd-bg)!important}
[data-testid="stBaseButton-primary"] p{color:var(--nd-bg)!important}
[data-testid="stBaseButton-primary"]:hover{background:var(--nd-text1)!important;border-color:var(--nd-text1)!important}
[data-testid="stBaseButton-secondary"]{background:transparent!important;border:1px solid var(--nd-border2)!important;
  color:var(--nd-text1)!important}
[data-testid="stBaseButton-secondary"]:hover{border-color:var(--nd-text1)!important;color:var(--nd-text0)!important}
[data-testid^="stBaseButton"]:disabled{opacity:.4}
/* ---------- contenedores: borde fino, sin sombras */
[data-testid="stExpander"] details{border:1px solid var(--nd-border)!important;border-radius:8px;background:transparent}
[data-testid="stExpander"] summary p{font-family:var(--nd-mono);font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--nd-text1)}
[data-testid="stExpander"] summary:hover p{color:var(--nd-text0)}
[data-testid="stAlertContainer"]{background:transparent!important;border:1px solid var(--nd-border2);color:var(--nd-text1)!important;
  border-radius:8px}
[data-testid="stAlertContainer"] p{color:var(--nd-text1)}
[data-testid="stAlertContentError"]::before,[data-testid="stAlertContentWarning"]::before,
[data-testid="stAlertContentSuccess"]::before,[data-testid="stAlertContentInfo"]::before{font-family:var(--nd-mono);
  font-size:11px;letter-spacing:.08em;display:block;margin-bottom:4px}
[data-testid="stAlertContentError"]::before{content:"[ERROR]";color:var(--nd-accent)}
[data-testid="stAlertContentWarning"]::before{content:"[ATENCIÓN]";color:#D4A843}
[data-testid="stAlertContentSuccess"]::before{content:"[OK]";color:#4A9E5C}
[data-testid="stAlertContentInfo"]::before{content:"[INFO]";color:var(--nd-text2)}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]){border-color:var(--nd-accent)}
*{box-shadow:none}
[data-testid="stSidebar"]{border-right:1px solid var(--nd-border)}
/* ---------- barra de avance de Streamlit: segmentada */
[data-testid="stProgressBarTrack"]{background:var(--nd-seg-off)!important;border-radius:0!important;height:8px!important}
[data-testid="stProgressBarTrack"]>div{border-radius:0!important;background-color:transparent!important;
  background-image:repeating-linear-gradient(90deg,var(--nd-text0) 0 10px,transparent 10px 12px)!important}
/* casilla marcada: palomita en color de fondo sobre el relleno de tinta */
[data-testid="stCheckbox"] svg polyline{stroke:var(--nd-bg)!important}
[data-testid="stProgress"] p{font-family:var(--nd-mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--nd-text2)}
/* ---------- piezas propias */
.nd-seg{display:block;width:100%}
.nd-seg-in{display:flex;gap:2px;height:100%}
.nd-seg-in i{flex:1;background:var(--nd-seg-off)}
.nd-seg-in i.on{background:var(--c,var(--nd-text0))}
.nd-credit{font-family:var(--nd-mono);text-transform:uppercase;color:var(--nd-text2);opacity:1!important}
.nd-brand{padding:0 0 32px 0}
.nd-logo{color:var(--nd-text0);line-height:0;margin:0 0 16px 0}
.nd-logo svg{display:block}
.nd-logo-txt{font-family:var(--nd-dot);font-size:44px;line-height:1;font-weight:800}
.nd-progress{margin:8px 0 8px 0}
.nd-rows{border-top:1px solid var(--nd-border)}
.nd-row{display:flex;justify-content:space-between;align-items:baseline;gap:16px;padding:12px 0;
  border-bottom:1px solid var(--nd-border)}
.nd-row-tight{padding:0 0 8px 0;border:0}
.nd-val{font-family:var(--nd-mono);font-size:18px;color:var(--nd-text1);white-space:nowrap}
.nd-unit{font-family:var(--nd-mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--nd-text2);
  margin-left:4px;vertical-align:.4em}
.nd-steps{list-style:none;margin:16px 0 0 0;padding:0}
.nd-steps li{display:grid;grid-template-columns:28px 1fr 16px;font-family:var(--nd-mono);font-size:11px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--nd-text3);padding:5px 0;margin:0}
.nd-steps li.on{color:var(--nd-text1)}
.nd-steps li b{font-weight:400;text-align:right}
.nd-hero{padding:8px 0 24px 0}
.nd-display{font-family:var(--nd-dot);font-size:96px;line-height:1;letter-spacing:-.03em;color:var(--nd-text0);
  font-weight:800;margin-top:12px}
.nd-display .nd-unit{font-size:12px;vertical-align:baseline;margin-left:12px}
.nd-note{font-family:var(--nd-mono);font-size:12px;letter-spacing:.04em;color:var(--nd-text2);margin-top:16px}
.nd-status{font-family:var(--nd-mono);font-size:12px;letter-spacing:.04em;color:var(--nd-text2)}
.nd-err{color:var(--nd-accent)}
"""


def _initial_key() -> str:
    q = st.query_params.get("estilo")
    if q in ESTILOS:
        return q
    env = os.environ.get("VIENTOMX_ESTILO", "")
    return env if env in ESTILOS else DEFAULT


def aplicar(SS, key: str = "ui_estilo") -> Estilo:
    """Lee el estilo elegido, ajusta el tema de Streamlit e inyecta el CSS. Llamar antes de dibujar nada."""
    if SS.get(key) not in ESTILOS:
        SS[key] = _initial_key()
    k = SS[key]
    if st.query_params.get("estilo") != k:
        if k == DEFAULT:
            st.query_params.pop("estilo", None)
        else:
            st.query_params["estilo"] = k
    original = _original_theme()
    nothing = k != CLASICA
    dark = k != "nothing-claro"
    if _set_streamlit_theme(_streamlit_theme(dark) if nothing else original):
        st.rerun()  # el tema de Streamlit se envía al navegador al inicio de cada ejecución
    ui = Estilo(key=k, nothing=nothing, dark=dark, t=TOKENS[dark])
    if nothing:
        st.html(f"<style>{ui.css()}</style>")
    return ui


def selector(key: str = "ui_estilo"):
    """Control para elegir el estilo (va en la barra lateral)."""
    st.selectbox("Estilo de la interfaz", list(ESTILOS), format_func=ESTILOS.get, key=key,
                 help="«Nothing»: variante monocroma inspirada en el sistema de diseño de Nothing, en modo "
                      "oscuro o claro. Se recuerda en la URL (?estilo=…).")
