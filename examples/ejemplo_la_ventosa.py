"""Ejemplo de uso como librería (sin interfaz): parque en La Ventosa, Oaxaca.

Requiere internet para descargar ERA5 (Open-Meteo) y el terreno (Copernicus/ESA).
    python examples/ejemplo_la_ventosa.py
"""
from vientomx import aep, climate, layout, met, report, terrain, turbines

lib = turbines.load_library()
turbina = lib["IEA Reference 3.4 MW D130"]
buje = 110.0

# 1) Layout: archivo propio o rejilla de ejemplo
lay = layout.grid_layout(16.55, -94.95, rows=3, cols=8, spacing_row_D=7, spacing_col_D=3,
                         rotor_d=turbina.rotor_diameter, orientation_deg=0)
# lay = layout.load_layout("mis_turbinas.kml")

# 2) Viento: 20 años de ERA5
lat0, lon0 = lay.lat.mean(), lay.lon.mean()
m = met.download_open_meteo_era5(lat0, lon0, 2005, 2024, progress=lambda f, s: print(s))
ts = climate.extrapolate_to_height(m, buje)
print(climate.sector_stats(ts).round(2))

# 3) Terreno y rugosidad
g, xs, ys = terrain.make_grid(lay.lat, lay.lon, buffer_km=8, dx=100)
terrain.download_dem(g)
terrain.download_worldcover(g)

# 4) AEP
rho = climate.air_density(m, float(g.sample(g.elev, xs, ys).mean()), buje)
cfg = aep.SimConfig(hub_height=buje, wake_model="jensen")
res = aep.run_aep(lay, ts, turbina, cfg, g, rho)
for k, v in res.summary.items():
    print(k, v)
open("resultados_la_ventosa.xlsx", "wb").write(report.results_to_excel(res))
