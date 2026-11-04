"""Pruebas del módulo de estilos de la interfaz (app/tema.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

tema = pytest.importorskip("tema")


def test_strip_symbols_quita_emoji_y_numera_pestanas():
    assert tema.strip_symbols("🔎 Buscar turbinas de OSM aquí") == "Buscar turbinas de OSM aquí"
    assert tema.strip_symbols("① Sitio y layout") == "01 Sitio y layout"
    assert tema.strip_symbols("🗑️ Borrar turbina") == "Borrar turbina"
    assert tema.strip_symbols("⬇ Guardar proyecto (ZIP)") == "Guardar proyecto (ZIP)"
    assert tema.strip_symbols("¿Overpass no responde?") == "¿Overpass no responde?"


def test_etiquetas_clasicas_sin_cambios():
    clasico = tema.Estilo()
    nothing = tema.Estilo(key="nothing-oscuro", nothing=True, dark=True, t=tema.TOKENS[True])
    assert clasico.L("▶ Calcular AEP") == "▶ Calcular AEP"
    assert nothing.L("▶ Calcular AEP") == "Calcular AEP"
    assert clasico.cs("seq", "Viridis") == "Viridis"
    assert nothing.cs("seq", "Viridis")[-1][1] == "#FFFFFF"
    assert nothing.ramp(3, ["a"])[0].startswith("rgba(255,255,255,0.15")


def test_seg_bar_y_filas():
    html = tema.seg_bar(0.5, 10)
    assert html.count('class="on"') == 5 and html.count("<i") == 10
    assert tema.seg_bar(2.0, 4).count('class="on"') == 4  # se recorta a 100 %
    rows = tema.stat_rows([("Capacidad <MW>", "22.0", "MW", None)])
    assert "&lt;MW&gt;" in rows  # el texto se escapa


def test_plantilla_plotly_y_recoloreo():
    import plotly.express as px

    fig = px.bar(x=[1, 2], y=[3, 4])
    tema._recolor_px_defaults(fig, "#FFFFFF")
    assert fig.data[0].marker.color == "#FFFFFF"
    tpl = tema.plotly_template(dark=False)
    assert tpl.layout.colorway[0] == "#000000"


def test_temas_de_streamlit_validos():
    from streamlit import config

    config.get_config_options()
    for dark in (True, False):
        for k in tema._streamlit_theme(dark):
            assert f"theme.{k}" in config._config_options_template, k
