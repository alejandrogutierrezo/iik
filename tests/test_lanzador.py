"""Pruebas del lanzador de escritorio (instalador/lanzador.py), sin levantar el servidor."""
import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lanzador", RAIZ / "instalador" / "lanzador.py")
lanzador = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lanzador)


def test_carpeta_base_tiene_la_app():
    base = lanzador.carpeta_base()
    assert (base / "app" / "streamlit_app.py").exists()
    assert (base / "brand" / "iik-icon-dark.svg").exists()


def test_puerto_libre_y_responde():
    p = lanzador.puerto_libre(0)
    assert 0 < p < 65536
    assert not lanzador.responde(p, 0.2)  # nadie escucha ahí


def test_candado_unico():
    a = lanzador.tomar_candado()
    if a is None:  # otro iik abierto en esta máquina: no se puede probar
        return
    try:
        assert lanzador.tomar_candado() is None
    finally:
        a.close()
