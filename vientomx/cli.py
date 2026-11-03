"""Línea de comandos: ``vientomx app`` abre la interfaz; ``vientomx demo`` corre un caso de prueba."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vientomx", description="Wind Resource Assessment para México")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("app", help="Abrir la interfaz gráfica en el navegador")
    sub.add_parser("demo", help="Correr el caso demo (sin internet) e imprimir resultados")
    a = ap.parse_args(argv)
    if a.cmd == "app":
        app = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
        sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))
    if a.cmd == "demo":
        from . import aep, climate, layout, met, terrain, turbines

        t = turbines.load_library()["IEA Reference 3.4 MW D130"]
        m = met.synthetic_met()
        lay = layout.grid_layout(16.55, -94.95, 3, 8, 7, 3, t.rotor_diameter)
        g, _, _ = terrain.make_grid(lay.lat, lay.lon, 5, 100)
        terrain.flat_terrain(g, 30, 0.05)
        cfg = aep.SimConfig(hub_height=110)
        r = aep.run_aep(lay, climate.extrapolate_to_height(m, 110), t, cfg, g, climate.air_density(m, 30, 110))
        for k, v in r.summary.items():
            print(f"{k:45s} {v:,.2f}" if isinstance(v, float) else f"{k:45s} {v}")


if __name__ == "__main__":
    main()
