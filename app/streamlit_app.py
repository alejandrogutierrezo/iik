"""VientoMX — interfaz gráfica (Streamlit).

Ejecutar:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vientomx import aep, climate, gwa, layout, met, osm, pdf_report, report, terrain, turbines, wtk  # noqa: E402

st.set_page_config(page_title="VientoMX · Evaluación de recurso eólico", page_icon="🌬️", layout="wide")

SS = st.session_state
for k, v in dict(layout=None, met=None, met_reanalysis=None, ref_latlon=None, grid=None, turbine=None,
                 hub_height=100.0, results=None, cfg=None, calib=None, mcp_info=None,
                 osm_turbines=None, osm_plants=None, osm_msg=None, map_center=None, map_zoom=None, map_bounds=None,
                 move_sel=None,
                 prev_obj_click=None, prev_map_click=None, layout_ver=0, calib_h=None, calib_ref=None,
                 wtk_points=None, gwa_info=None).items():
    SS.setdefault(k, v)

# Longitudes fuera de [-180, 180) (p. ej. de clics en otra "copia" del mundo en el mapa) rompen las descargas
if SS.layout is not None and not SS.layout.empty and (SS.layout.lon.abs() > 180).any():
    SS.layout = SS.layout.assign(lon=layout.wrap_lon(SS.layout.lon.to_numpy(float)))
    SS.layout_ver += 1

COLORS = px.colors.sequential.Viridis


@st.cache_resource(show_spinner="Cargando librería de aerogeneradores…")
def get_library():
    return turbines.load_library()


def progress_cb(bar):
    def cb(frac, msg):
        bar.progress(min(max(frac, 0.0), 1.0), text=msg)
    return cb


def ok(flag):
    return "✅" if flag else "⬜"


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _osm_search(south, west, north, east):
    return osm.fetch_osm(south, west, north, east)


def _fit(lat, lon):
    """Centro y zoom del mapa folium (Leaflet, mosaicos de 256 px) que encuadran las turbinas."""
    (clat, clon), z = layout.map_view(lat, lon, width_px=900, height_px=560, tile_px=256, max_zoom=16)
    return [clat, clon], int(z)


def _map_editor():
    """Mapa interactivo (folium): colocar, mover y borrar turbinas con clic, e importar las de OSM."""
    import folium
    from streamlit_folium import st_folium

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        site = st.selectbox("Ir a la región", ["(mantener vista actual)"] + list(layout.MEXICO_SITES) +
                            ["Coordenadas propias"], key="map_site")
    with c2:
        if site == "Coordenadas propias":
            cc1, cc2 = st.columns(2)
            glat = cc1.number_input("Latitud", value=25.60, format="%.5f", key="map_lat")
            glon = cc2.number_input("Longitud", value=-100.70, format="%.5f", key="map_lon")
        elif site in layout.MEXICO_SITES:
            glat, glon = layout.MEXICO_SITES[site]
        else:
            glat = glon = None
        base = st.radio("Fondo", ["OpenStreetMap", "Satélite (Esri)"], horizontal=True, key="map_base")
    with c3:
        st.write("")
        if st.button("Centrar mapa", disabled=glat is None):
            SS.map_center, SS.map_zoom = [glat, glon], 14
        if st.button("Centrar en layout", disabled=SS.layout is None or SS.layout.empty):
            SS.map_center, SS.map_zoom = _fit(SS.layout.lat, SS.layout.lon)

    action = st.radio("Al hacer clic en el mapa:", ["➕ Agregar turbina", "✋ Mover turbina", "🗑️ Borrar turbina",
                                                   "🔍 Solo navegar"], horizontal=True, key="map_action",
                      help="Agregar: clic sobre un punto naranja (turbina de OSM) la toma con su posición exacta; "
                           "clic en otro lugar coloca una turbina nueva. Mover: primer clic elige la turbina, el "
                           "segundo la coloca. Borrar: clic sobre la turbina.")

    if not action.startswith("✋") or SS.layout is None or SS.move_sel not in SS.layout.index:
        SS.move_sel = None

    # ---- capa de turbinas OSM
    b1, b2 = st.columns([2, 3])
    with b1:
        if st.button("🔎 Buscar turbinas de OSM aquí", type="primary"):
            bnd = SS.map_bounds
            if not bnd:
                st.warning("Mueva o acerque el mapa una vez para conocer el área visible.")
            else:
                s_, w_, n_, e_ = bnd
                note = ""
                if (n_ - s_) * (e_ - w_) > 0.25:  # vista muy abierta: buscar ±12 km alrededor del centro
                    s_, w_, n_, e_ = osm.bbox_around((s_ + n_) / 2, (w_ + e_) / 2, 12)
                    note = " (vista muy amplia: se buscó en ±12 km alrededor del centro del mapa)"
                try:
                    with st.spinner(f"Consultando OpenStreetMap (Overpass, máx. {osm.DEADLINE_S} s)…"):
                        tur, plants = _osm_search(round(s_, 4), round(w_, 4), round(n_, 4), round(e_, 4))
                    SS.osm_turbines, SS.osm_plants = tur, plants
                    n = len(tur)
                    if n:
                        SS.osm_msg = ("success", f"{n} aerogeneradores encontrados en OSM{note}. Aparecen como "
                                                 "puntos naranjas: clic en uno para usarlo, o «Aceptar las … turbinas».")
                        SS.map_center, SS.map_zoom = _fit(tur.lat, tur.lon)
                    else:
                        txt = "No hay aerogeneradores individuales mapeados en OSM en esta zona" + note + "."
                        if len(plants):
                            names = ", ".join(str(x) for x in plants.nombre.fillna("(sin nombre)").head(5))
                            txt += (f" Sí está mapeado el polígono del parque ({names}), pero sin las turbinas: "
                                    "colóquelas con clic sobre el fondo **Satélite (Esri)**.")
                        else:
                            txt += " Colóquelas con clic sobre el fondo **Satélite (Esri)**."
                        SS.osm_msg = ("info", txt)
                except Exception as e:
                    SS.osm_msg = ("error", str(e))
                st.rerun()
    if SS.get("osm_msg"):
        kind, txt = SS.osm_msg
        getattr(st, kind)(txt)
    with st.expander("¿Overpass no responde en su red? Importar turbinas exportadas de overpass-turbo (GeoJSON)"):
        st.markdown("1. Abra [overpass-turbo.eu](https://overpass-turbo.eu), navegue al parque y ejecute esta consulta. "
                    "2. **Exportar → GeoJSON → descargar**. 3. Suba el archivo aquí.")
        st.code('[out:json][timeout:25];\n(nwr["power"="generator"]["generator:source"="wind"]({{bbox}});\n'
                ' nwr["power"="generator"]["generator:method"="wind_turbine"]({{bbox}}););\nout center;',
                language="text")
        gj = st.file_uploader("GeoJSON de overpass-turbo", type=["geojson", "json"], key="osm_gj")
        if gj is not None and st.button("Cargar turbinas del GeoJSON"):
            import json
            try:
                tur = osm.parse_geojson(json.loads(gj.getvalue()))
                SS.osm_turbines = tur
                SS.osm_msg = ("success" if len(tur) else "info",
                              f"{len(tur)} aerogeneradores leídos del GeoJSON." if len(tur) else
                              "El GeoJSON no contiene puntos de aerogeneradores.")
                if len(tur):
                    SS.map_center, SS.map_zoom = _fit(tur.lat, tur.lon)
            except Exception as e:
                SS.osm_msg = ("error", f"No se pudo leer el GeoJSON: {e}")
            st.rerun()
    osm_df = SS.osm_turbines
    in_layout = set()
    if SS.layout is not None and "osm_id" in SS.layout:
        in_layout = set(SS.layout.osm_id.dropna().astype(str))
    pending = osm_df[~osm_df.osm_id.isin(in_layout)] if osm_df is not None and not osm_df.empty else None
    if osm_df is not None and not osm_df.empty:
        n_used = len(osm_df) - (len(pending) if pending is not None else 0)
        with st.expander(f"Tabla de turbinas de OSM ({len(osm_df)}; {n_used} ya en el layout)"):
            st.dataframe(osm_df.assign(en_layout=osm_df.osm_id.isin(in_layout)), hide_index=True,
                         column_config={"lat": st.column_config.NumberColumn(format="%.6f"),
                                        "lon": st.column_config.NumberColumn(format="%.6f")})
    if pending is not None and len(pending):
        a1, a2 = st.columns([3, 2])
        a2.caption("O bien, en modo «➕ Agregar turbina», haga clic sobre cada punto naranja para tomarlas una por una.")
        if a1.button(f"✅ Aceptar las {len(pending)} turbinas de OSM y agregarlas al layout", type="primary",
                     use_container_width=True, key="osm_accept_all"):
            new = osm.to_layout(pending)
            base_df = SS.layout
            for r in new.itertuples(index=False):
                extra = {"osm_id": r.osm_id}
                if "hub_height" in new and pd.notna(getattr(r, "hub_height", None)):
                    extra["hub_height"] = r.hub_height
                base_df = layout.add_turbine(base_df, r.lat, r.lon, r.id, **extra)
            SS.layout, SS.results, SS.move_sel = base_df, None, None
            SS.layout_ver += 1
            SS.osm_msg = ("success", f"{len(new)} turbinas de OSM agregadas al layout (ahora {len(base_df)} en total). "
                                     "Revise o corrija sus coordenadas en la tabla del layout.")
            SS.map_center, SS.map_zoom = _fit(base_df.lat, base_df.lon)
            st.rerun()
    with b2:
        if SS.layout is not None and len(SS.layout) and st.button("Borrar todo el layout"):
            SS.layout, SS.results, SS.move_sel = None, None, None
            SS.layout_ver += 1
            st.rerun()

    # ---- mapa
    center = SS.map_center or ([float(SS.layout.lat.mean()), float(SS.layout.lon.mean())]
                               if SS.layout is not None and len(SS.layout) else [23.5, -102.0])
    zoom = SS.map_zoom or (14 if SS.layout is not None and len(SS.layout) else 5)
    if base.startswith("Sat"):
        m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True, world_copy_jump=True)
        folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                         attr="Esri, Maxar, Earthstar Geographics", name="Satélite", max_zoom=19).add_to(m)
    else:
        m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap", control_scale=True, max_zoom=19,
                       world_copy_jump=True)
    fg = folium.FeatureGroup(name="turbinas")
    if pending is not None:
        for r in pending.itertuples(index=False):
            tip = " · ".join(str(v) for v in (r.ref or r.nombre, r.fabricante, r.modelo) if v) or r.osm_id
            folium.CircleMarker([r.lat, r.lon], radius=8, color="#c2410c", weight=2, fill=True, fill_color="#fb923c",
                                fill_opacity=0.8, tooltip=f"OSM: {tip} (clic para usar)").add_to(fg)
    if SS.layout is not None:
        for i, r in SS.layout.iterrows():
            sel = SS.move_sel is not None and i == SS.move_sel
            folium.CircleMarker([r.lat, r.lon], radius=8, color="#facc15" if sel else "#1e3a8a", weight=3 if sel else 2,
                                fill=True, fill_color="#3b82f6", fill_opacity=0.9, tooltip=str(r["id"])).add_to(fg)
    out = st_folium(m, key="layout_map", height=560, use_container_width=True, center=center, zoom=zoom,
                    feature_group_to_add=fg, wrap_longitude=True,
                    returned_objects=["last_clicked", "last_object_clicked", "bounds", "zoom"]) or {}

    bnd, z = out.get("bounds"), out.get("zoom")
    if bnd and bnd.get("_southWest") and bnd["_southWest"].get("lat") is not None:
        sw, ne = bnd["_southWest"], bnd["_northEast"]
        w, e = layout.wrap_lon(sw["lng"]), layout.wrap_lon(ne["lng"])
        SS.map_bounds = (sw["lat"], w, ne["lat"], e) if w < e else None  # None si la vista cruza el antimeridiano
    z = z or zoom

    # ---- procesar el clic más reciente (sin repetir el mismo en cada rerun)
    click = None
    obj, pt = out.get("last_object_clicked"), out.get("last_clicked")
    if obj and obj != SS.prev_obj_click:
        click = obj
    elif pt and pt != SS.prev_map_click:
        click = pt
    SS.prev_obj_click, SS.prev_map_click = obj, pt
    if click is None or action.startswith("🔍"):
        if action.startswith("✋") and SS.move_sel is not None:
            st.caption(f"Turbina seleccionada: **{SS.layout.loc[SS.move_sel, 'id']}**. Haga clic en su nueva posición.")
        return
    lat, lon = float(click["lat"]), layout.wrap_lon(click["lng"])
    snap = 14 * 156543.03 * np.cos(np.radians(lat)) / 2 ** z  # ~14 px en metros
    i_lay, d_lay = layout.nearest(SS.layout, lat, lon)
    if action.startswith("➕"):
        i_osm, d_osm = layout.nearest(pending, lat, lon)
        if i_osm is not None and d_osm <= snap:
            r = osm.to_layout(pending.loc[[i_osm]]).iloc[0]
            extra = {"osm_id": r.osm_id}
            if "hub_height" in r and pd.notna(r.hub_height):
                extra["hub_height"] = r.hub_height
            SS.layout = layout.add_turbine(SS.layout, r.lat, r.lon, r["id"], **extra)
        elif d_lay > snap:
            SS.layout = layout.add_turbine(SS.layout, lat, lon)
        else:
            return
    elif action.startswith("🗑️"):
        if i_lay is None or d_lay > snap:
            return
        SS.layout = layout.remove_turbine(SS.layout, i_lay)
        if SS.layout.empty:
            SS.layout = None
    else:  # mover
        if SS.move_sel is None:
            if i_lay is None or d_lay > snap:
                return
            SS.move_sel = i_lay
        else:
            SS.layout = layout.move_turbine(SS.layout, SS.move_sel, lat, lon)
            SS.move_sel = None
    SS.results = None
    SS.layout_ver += 1
    st.rerun()


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🌬️ VientoMX")
    st.caption("Evaluación de recurso eólico y producción anual para parques eólicos en México.")
    st.markdown(
        f"""
**Avance del estudio**

- {ok(SS.layout is not None)} 1 · Sitio y layout
- {ok(SS.met is not None)} 2 · Datos de viento
- {ok(SS.grid is not None and SS.grid.elev is not None)} 3 · Terreno y rugosidad
- {ok(SS.turbine is not None)} 4 · Aerogenerador
- {ok(SS.results is not None)} 5 · Simulación
"""
    )
    st.divider()
    if st.button("Cargar caso demo (sin internet)", help="La Ventosa, Oaxaca: layout 3x8, viento sintético y terreno plano."):
        lib = get_library()
        SS.turbine = lib.get("IEA Reference 3.4 MW D130")
        SS.hub_height = 110.0
        SS.layout = layout.grid_layout(16.55, -94.95, 3, 8, 7, 3, 130, 0)
        SS.layout_ver += 1
        SS.met = met.synthetic_met()
        SS.ref_latlon = None
        g, _, _ = terrain.make_grid(SS.layout.lat, SS.layout.lon, 8, 100)
        terrain.flat_terrain(g, 30, 0.05)
        SS.grid = g
        SS.results = None
        st.rerun()
    st.caption("Datos: ERA5 (Copernicus/ECMWF), NASA POWER, Copernicus DEM, ESA WorldCover, NREL turbine-models, OEDB.")

tabs = st.tabs(["① Sitio y layout", "② Viento", "③ Terreno", "④ Aerogenerador", "⑤ Simulación", "⑥ Resultados",
                "📖 Método"])

# ------------------------------------------------------------------ 1. layout
with tabs[0]:
    st.header("Sitio y ubicación de aerogeneradores")
    mode = st.radio("¿Cómo quiere definir el layout?", ["Seleccionar en el mapa (OpenStreetMap)",
                                                         "Subir archivo de coordenadas", "Generar layout rectangular"],
                    horizontal=True)
    if mode.startswith("Seleccionar"):
        st.caption("Navegue hasta el parque. Si OpenStreetMap ya tiene las turbinas mapeadas (icono de aerogenerador), "
                   "pulse **Buscar turbinas de OSM aquí** y úselas con un clic o todas a la vez; si no, colóquelas "
                   "haciendo clic. Datos de turbinas © colaboradores de OpenStreetMap (ODbL).")
        _map_editor()
    elif mode.startswith("Subir"):
        c1, c2 = st.columns([2, 1])
        with c1:
            up = st.file_uploader("CSV, Excel o Google Earth (KML/KMZ)", type=["csv", "txt", "xlsx", "xls", "kml", "kmz"])
        with c2:
            zone = st.number_input("Zona UTM (solo si sus coordenadas son x/y en metros)", 0, 60, 0,
                                   help="México está entre las zonas 11 (Baja California) y 16 (Yucatán). 0 = no aplica.")
            tmpl = pd.DataFrame({"id": ["WTG01", "WTG02"], "lat": [16.55, 16.56], "lon": [-94.95, -94.95]})
            st.download_button("Descargar plantilla CSV", tmpl.to_csv(index=False), "layout_plantilla.csv")
        if up is not None and SS.get("layout_file") != (up.file_id, zone):
            try:
                df = layout.load_layout(up, up.name, utm_zone=zone or None)
                SS.layout_file = (up.file_id, zone)
                SS.layout = df
                SS.layout_ver += 1
                SS.results = None
                if df.attrs.get("warning"):
                    st.warning(df.attrs["warning"])
                st.success(f"{len(df)} aerogeneradores cargados.")
            except Exception as e:
                st.error(f"No se pudo leer el archivo: {e}")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            site = st.selectbox("Región", list(layout.MEXICO_SITES) + ["Coordenadas propias"])
            if site == "Coordenadas propias":
                lat_c = st.number_input("Latitud", value=16.55, format="%.5f")
                lon_c = st.number_input("Longitud", value=-94.95, format="%.5f")
            else:
                lat_c, lon_c = layout.MEXICO_SITES[site]
        with c2:
            rows = st.number_input("Filas", 1, 30, 3)
            cols = st.number_input("Turbinas por fila", 1, 50, 8)
            d_rot = st.number_input("Diámetro de rotor [m]", 20.0, 300.0,
                                    float(SS.turbine.rotor_diameter) if SS.turbine else 130.0)
        with c3:
            s_row = st.number_input("Separación entre filas [D]", 2.0, 20.0, 7.0)
            s_col = st.number_input("Separación dentro de la fila [D]", 1.5, 15.0, 3.0)
            orient = st.number_input("Dirección dominante del viento [°]", 0.0, 359.0, 0.0,
                                     help="Las filas se colocan perpendiculares a esta dirección.")
        if st.button("Generar layout", type="primary"):
            SS.layout = layout.grid_layout(lat_c, lon_c, int(rows), int(cols), s_row, s_col, d_rot, orient)
            SS.layout_ver += 1
            SS.results = None

    if SS.layout is not None:
        st.subheader(f"Layout: {len(SS.layout)} aerogeneradores")
        on_map = mode.startswith("Seleccionar")
        c1, c2 = st.columns([1, 1] if on_map else [1, 2])
        with c1:
            edited = st.data_editor(SS.layout, num_rows="dynamic", height=380, key=f"layout_editor_{SS.layout_ver}")
            if not edited.equals(SS.layout):
                SS.layout = edited.dropna(subset=["lat", "lon"]).reset_index(drop=True)
                SS.results = None
                SS.layout_ver += 1  # reinicia el editor para no re-aplicar sus cambios sobre la tabla nueva
                st.rerun()
        with c2:
            st.download_button("Descargar layout (CSV)", SS.layout.to_csv(index=False), "layout.csv")
            if on_map:
                st.caption("Las coordenadas se pueden corregir en la tabla; el mapa de arriba se actualiza. "
                           "La columna osm_id indica las turbinas tomadas de OpenStreetMap.")
                if SS.turbine is not None and len(SS.layout) > 1:
                    from scipy.spatial import cKDTree
                    from vientomx.terrain import utm_crs
                    from pyproj import Transformer
                    tr = Transformer.from_crs(4326, utm_crs(SS.layout.lat.mean(), SS.layout.lon.mean()), always_xy=True)
                    xy = np.column_stack(tr.transform(SS.layout.lon.to_numpy(), SS.layout.lat.to_numpy()))
                    dmin = cKDTree(xy).query(xy, k=2)[0][:, 1].min() / SS.turbine.rotor_diameter
                    (st.warning if dmin < 2 else st.caption)(f"Separación mínima entre turbinas: {dmin:.1f} D")
            else:
                (clat, clon), zm = layout.map_view(SS.layout.lat, SS.layout.lon, height_px=420)
                fig = px.scatter_map(SS.layout, lat="lat", lon="lon", hover_name="id", zoom=zm, height=420,
                                     center=dict(lat=clat, lon=clon), map_style="open-street-map")
                fig.update_traces(marker=dict(size=11, color="#1f77b4"))
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig)

# ------------------------------------------------------------------ 2. viento
with tabs[1]:
    st.header("Datos meteorológicos")
    if SS.layout is None:
        st.info("Primero defina el layout (paso 1) para ubicar el punto de referencia.")
    else:
        lat0, lon0 = float(SS.layout.lat.mean()), float(SS.layout.lon.mean())
        src = st.selectbox("Fuente de datos", [
            "ERA5 vía Open-Meteo (sin registro, recomendado)",
            "NREL WIND Toolkit México — WRF ~2 km, 2007–2014 (sin registro; mejor en montaña)",
            "NASA POWER / MERRA-2 (sin registro)",
            "ERA5 desde Copernicus CDS (requiere API key)",
            "Medición propia: torre o LiDAR (CSV)",
        ])
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            plat = st.number_input("Latitud del punto", value=round(lat0, 4), format="%.4f")
        with c2:
            plon = st.number_input("Longitud del punto", value=round(lon0, 4), format="%.4f")
        is_wtk = "WIND Toolkit" in src
        if not src.startswith("Medición"):
            with c3:
                if is_wtk:
                    y0 = st.number_input("Año inicial", 2007, 2014, 2007)
                else:
                    y0 = st.number_input("Año inicial", 1981 if "NASA" not in src else 2001, 2025, 2005)
            with c4:
                if is_wtk:
                    y1 = st.number_input("Año final", 2007, 2014, 2013,
                                         help="2014 está en otra versión del archivo: requiere indexarlo aparte y "
                                              "descarga ~4 veces más datos.")
                else:
                    y1 = st.number_input("Año final", 1981, 2025, 2024)
            wtk_gid = None
            if is_wtk:
                st.caption("Modelo de mesoescala WRF (3Tier/Vaisala para el NREL), malla de ~2 km, horario, 10–200 m. "
                           "Resuelve mucho mejor que ERA5 los pasos de montaña, cañones y brisas. Se descargan solo los "
                           "bloques del punto elegido (~40 MB por año; 2014 ~160 MB). La primera descarga de un punto indexa el "
                           "archivo (unos minutos); después es mucho más rápida. Datos: NREL, AWS Open Data.")
                if st.button("Buscar puntos de malla cercanos"):
                    with st.spinner("Leyendo la malla del WIND Toolkit…"):
                        try:
                            SS.wtk_points = wtk.nearest_points(plat, plon, 9)
                        except Exception as e:
                            st.error(f"No se pudo leer el WIND Toolkit: {e}")
                if SS.wtk_points is not None:
                    pts = SS.wtk_points.copy()
                    elev_t = None
                    if SS.grid is not None and SS.grid.elev is not None:
                        from pyproj import Transformer as _Tr
                        _t = _Tr.from_crs(4326, SS.grid.crs, always_xy=True)
                        _x, _y = _t.transform(SS.layout.lon.to_numpy(), SS.layout.lat.to_numpy())
                        elev_t = float(np.nanmean(SS.grid.sample(SS.grid.elev, _x, _y)))
                    best_z = None
                    if elev_t is not None:  # punto cercano con elevación parecida a las turbinas
                        best_z = int(np.argmin(pts.dist_km + np.abs(pts.elev_m - elev_t) / 100.0))
                    labels = [f"{r.gid} · {r.dist_km:.1f} km · elev. modelo {r.elev_m:.0f} m"
                              + (" · más cercano" if i == 0 else "") + (" · elevación similar" if i == best_z else "")
                              for i, r in enumerate(pts.itertuples())]
                    sel = st.selectbox("Punto de malla", labels, index=0,
                                       help="La malla de 2 km suaviza el relieve: compare la elevación del modelo con "
                                            "la de sus turbinas" + (f" (≈{elev_t:.0f} m según el DEM)." if elev_t else
                                                                    " (prepare el terreno en el paso 3 para verla aquí)."))
                    wtk_gid = int(pts.gid.iloc[labels.index(sel)])
                    hh = min(wtk.HEIGHTS, key=lambda z: abs(z - float(SS.hub_height)))
                    if st.button(f"Comparar velocidad media de los {len(pts)} puntos (año 2013, {hh} m, ~30 MB)"):
                        cbar = st.progress(0.0, text="Comparando puntos…")
                        if True:
                            try:
                                cmp_ = wtk.compare_points(pts.gid.tolist(), 2013, hh, progress=progress_cb(cbar))
                                SS.wtk_points = pts = pts.drop(columns=[c for c in pts if c.startswith("v_media")]
                                                               ).merge(cmp_, on="gid")
                            except Exception as e:
                                st.error(f"No se pudo comparar: {e}")
                    st.dataframe(pts.round({"lat": 4, "lon": 4, "dist_km": 2}).round(2), hide_index=True, height=230)
                    st.caption("En terreno complejo, puntos a 2 km pueden diferir más de 1 m/s. Elija el que mejor "
                               "represente la posición y elevación de las turbinas (o compárelos con una medición).")
            key_cds = None
            if "CDS" in src:
                key_cds = st.text_input("API key del CDS (o configure ~/.cdsapirc)", type="password")
                st.caption("Regístrese gratis en https://cds.climate.copernicus.eu y acepte la licencia de ERA5.")
            st.caption("Se recomiendan ≥ 10 años para representar el largo plazo. La descarga se guarda en caché.")
            if st.button("Descargar datos", type="primary"):
                bar = st.progress(0.0, text="Conectando…")
                try:
                    if is_wtk:
                        m = wtk.download_wtk_mexico(plat, plon, int(y0), int(y1), float(SS.hub_height), gid=wtk_gid,
                                                    progress=progress_cb(bar))
                    elif "Open-Meteo" in src:
                        m = met.download_open_meteo_era5(plat, plon, int(y0), int(y1), progress=progress_cb(bar))
                    elif "NASA" in src:
                        m = met.download_nasa_power(plat, plon, int(y0), int(y1), progress=progress_cb(bar))
                    else:
                        m = met.download_era5_cds(plat, plon, int(y0), int(y1), progress=progress_cb(bar),
                                                  url="https://cds.climate.copernicus.eu/api" if key_cds else None,
                                                  key=key_cds or None)
                    SS.met, SS.met_reanalysis, SS.ref_latlon, SS.results, SS.calib = m, m, None, None, None
                    SS.calib_h, SS.calib_ref, SS.gwa_info = None, None, None
                    bar.progress(1.0, text=f"Listo: {len(m.df):,} horas")
                except Exception as e:
                    st.error(f"Error en la descarga: {e}")
        else:
            up = st.file_uploader("CSV con marca de tiempo, velocidad(es) y dirección", type=["csv", "txt"])
            if up is not None:
                cols = met.measured_csv_columns(up)
                c1, c2, c3 = st.columns(3)
                with c1:
                    tcol = st.selectbox("Columna de fecha/hora", cols)
                    tz = st.selectbox("Zona horaria de los registros",
                                      ["UTC", "America/Mexico_City", "America/Monterrey", "America/Cancun",
                                       "America/Tijuana", "America/Hermosillo", "Etc/GMT+6"])
                with c2:
                    wscols = st.multiselect("Columnas de velocidad", cols)
                    heights = {c: st.number_input(f"Altura de '{c}' [m]", 1.0, 300.0, 80.0, key=f"h_{c}") for c in wscols}
                with c3:
                    wdcol = st.selectbox("Columna de dirección", cols)
                    st.caption("La posición de la torre es el punto de referencia (lat/lon arriba).")
                if st.button("Cargar medición", type="primary") and wscols:
                    try:
                        mm = met.load_measured_csv(up, tcol, {heights[c]: c for c in wscols}, wdcol, lat=plat, lon=plon, tz=tz)
                        SS.met, SS.ref_latlon, SS.results, SS.calib = mm, (plat, plon), None, None
                        st.success(f"Medición cargada: {len(mm.df):,} horas.")
                    except Exception as e:
                        st.error(f"No se pudo leer la medición: {e}")
            if SS.met is not None and SS.met.source.startswith("Medición") and SS.met_reanalysis is not None:
                st.subheader("Ajuste de largo plazo (MCP)")
                st.caption("Correlaciona la medición con el reanálisis descargado y genera una serie de largo plazo.")
                if st.button("Aplicar MCP (razón de varianzas por sector)"):
                    try:
                        hh = SS.met.heights[-1]
                        meas = SS.met.df.rename(columns={f"ws_{hh:g}": "ws", f"wd_{hh:g}": "wd"})
                        ref = climate.extrapolate_to_height(SS.met_reanalysis, hh)
                        lt, info = climate.mcp_variance_ratio(meas, ref)
                        SS.met = met.MetData(lt.rename(columns={"ws": f"ws_{hh:g}", "wd": f"wd_{hh:g}"}),
                                             SS.met.lat, SS.met.lon, f"MCP medición + {SS.met_reanalysis.source}",
                                             SS.met_reanalysis.elevation)
                        SS.mcp_info = info
                        st.success(f"MCP aplicado. R² global = {info.attrs['r2_total']:.2f}; giro medio = {info.attrs['veer']:.1f}°")
                        st.dataframe(info.round(3), hide_index=True)
                    except Exception as e:
                        st.error(str(e))
            elif SS.met is not None and SS.met.source.startswith("Medición"):
                st.info("Para el ajuste de largo plazo (MCP), descargue primero un reanálisis en este mismo paso y "
                        "luego vuelva a cargar la medición.")

    if SS.met is not None:
        m = SS.met
        hub = float(SS.hub_height)
        st.subheader(f"Análisis a {hub:g} m — {m.source}")
        ts = climate.extrapolate_to_height(m, hub)
        if climate.is_coarse_source(m.source):
            st.info("Esta fuente es un reanálisis de malla gruesa (25–50 km): en sierras, cañones y pasos de montaña "
                    "suele subestimar mucho el viento. Use el WIND Toolkit (2 km) o calibre con el Global Wind Atlas o "
                    "una medición.")
        with st.expander("Calibración a una velocidad media objetivo (Global Wind Atlas o medición)",
                         expanded=bool(SS.calib)):
            st.caption("Escala la serie para que su media coincida con un valor de referencia, conservando la forma de la "
                       "distribución, la rosa y los ciclos. La referencia se toma en el centro del parque y el modelo de "
                       "flujo calcula las diferencias entre turbinas respecto a ese punto.")
            c1, c2, c3 = st.columns(3)
            hopts = sorted(set(gwa.HEIGHTS) | {int(round(hub))})
            with c1:
                ch = st.selectbox("Altura del valor objetivo [m]", hopts,
                                  index=hopts.index(int(SS.calib_h or gwa.nearest_height(hub))))
            with c2:
                if st.button("Obtener del Global Wind Atlas", help="Velocidad media a la altura elegida en el centro "
                                                                   "del parque (resolución 250 m)."):
                    gh = gwa.nearest_height(ch)
                    bar = st.progress(0.0, text="Global Wind Atlas…")
                    try:
                        latc, lonc = float(SS.layout.lat.mean()), float(SS.layout.lon.mean())
                        pts_lat = np.r_[latc, SS.layout.lat.to_numpy(float)]
                        pts_lon = np.r_[lonc, SS.layout.lon.to_numpy(float)]
                        v = gwa.fetch_mean_speed(pts_lat, pts_lon, gh, progress=progress_cb(bar))
                        SS.gwa_info = {"height": gh, "center": float(v[0]), "turbines": v[1:].tolist()}
                        SS.calib, SS.calib_h, SS.calib_ref, SS.results = float(v[0]), gh, (latc, lonc), None
                        bar.progress(1.0, text=f"GWA {gh} m en el centro del parque: {v[0]:.2f} m/s")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                up_gwa = st.file_uploader("…o subir GeoTIFF del GWA (velocidad media)", type=["tif", "tiff"],
                                          key="gwa_up")
                if up_gwa is not None and SS.get("gwa_file") != up_gwa.file_id:
                    try:
                        latc, lonc = float(SS.layout.lat.mean()), float(SS.layout.lon.mean())
                        v = gwa.sample_raster(up_gwa.getvalue(), np.r_[latc, SS.layout.lat.to_numpy(float)],
                                              np.r_[lonc, SS.layout.lon.to_numpy(float)])
                        if not np.isfinite(v[0]):
                            raise ValueError("el archivo no cubre el centro del parque")
                        SS.gwa_file = up_gwa.file_id
                        SS.gwa_info = {"height": int(ch), "center": float(v[0]), "turbines": v[1:].tolist()}
                        SS.calib, SS.calib_h, SS.calib_ref, SS.results = float(v[0]), int(ch), (latc, lonc), None
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo leer el GeoTIFF: {e}")
            with c3:
                tgt = st.number_input("Velocidad media objetivo [m/s] (0 = sin calibrar)", 0.0, 20.0,
                                      float(SS.calib or 0.0), 0.01, key=f"calib_in_{SS.calib}_{SS.calib_h}")
            if (tgt or None) != SS.calib or (tgt and int(ch) != int(SS.calib_h or ch)):
                SS.calib, SS.calib_h, SS.results = (tgt or None), int(ch), None
                SS.calib_ref = SS.calib_ref or (float(SS.layout.lat.mean()), float(SS.layout.lon.mean()))
            if SS.gwa_info:
                gi = SS.gwa_info
                st.caption(f"Global Wind Atlas a {gi['height']} m: centro del parque {gi['center']:.2f} m/s; turbinas "
                           f"{np.nanmin(gi['turbines']):.2f}–{np.nanmax(gi['turbines']):.2f} m/s.")
        if SS.calib:
            ts, f = climate.calibrate_series(m, ts, SS.calib, SS.calib_h or hub)
            st.caption(f"Calibrado a {SS.calib:.2f} m/s a {SS.calib_h or hub:g} m · factor aplicado: {f:.3f}")
        k1, k2, k3, k4 = st.columns(4)
        A, kk = climate.weibull_fit(ts.ws)
        k1.metric("Velocidad media", f"{ts.ws.mean():.2f} m/s")
        k2.metric("Weibull A / k", f"{A:.2f} / {kk:.2f}")
        k3.metric("Densidad de potencia", f"{0.5*1.225*np.mean(ts.ws**3):.0f} W/m²")
        k4.metric("Variabilidad interanual", f"{100*climate.interannual_variability(ts):.1f} %")

        c1, c2 = st.columns(2)
        with c1:
            rose = climate.wind_rose_table(ts)
            fig = go.Figure()
            for i, col in enumerate(rose.columns):
                fig.add_trace(go.Barpolar(r=rose[col], theta=rose.index, name=f"{col.left:g}–{col.right:g} m/s",
                                          marker_color=COLORS[min(i * 2, len(COLORS) - 1)]))
            fig.update_layout(title="Rosa de vientos (% del tiempo)", polar=dict(angularaxis=dict(direction="clockwise", rotation=90)),
                              height=420, legend=dict(font=dict(size=10)))
            st.plotly_chart(fig)
        with c2:
            hist = np.histogram(ts.ws, bins=np.arange(0, 30.5, 0.5), density=True)
            xx = (hist[1][1:] + hist[1][:-1]) / 2
            from scipy import stats
            fig = go.Figure([go.Bar(x=xx, y=hist[0], name="Datos", marker_color="#4c78a8"),
                             go.Scatter(x=xx, y=stats.weibull_min.pdf(xx, kk, scale=A), name=f"Weibull A={A:.2f}, k={kk:.2f}",
                                        line=dict(color="#e45756", width=3))])
            fig.update_layout(title="Distribución de velocidades", xaxis_title="m/s", yaxis_title="densidad", height=420)
            st.plotly_chart(fig)
        c1, c2, c3 = st.columns(3)
        local = ts.ws.tz_convert("America/Mexico_City")
        with c1:
            mo = local.groupby(local.index.month).mean()
            st.plotly_chart(px.bar(x=mo.index, y=mo.values, labels={"x": "Mes", "y": "m/s"}, title="Ciclo anual", height=320))
        with c2:
            hr = local.groupby(local.index.hour).mean()
            st.plotly_chart(px.line(x=hr.index, y=hr.values, markers=True, labels={"x": "Hora local", "y": "m/s"},
                                    title="Ciclo diario", height=320))
        with c3:
            am = climate.annual_means(ts)
            st.plotly_chart(px.bar(x=am.index, y=am.values, labels={"x": "Año", "y": "m/s"}, title="Media anual", height=320))
        with st.expander("Estadística por sector y cizalladura"):
            st.dataframe(climate.sector_stats(ts).round(2), hide_index=True)
            if len(m.heights) >= 2:
                sh = climate.shear_table(m)
                sh.index = [f"{i*30}°" for i in sh.index]
                st.plotly_chart(px.imshow(sh, aspect="auto", color_continuous_scale="RdBu_r",
                                          labels=dict(x="Hora local", y="Sector", color="α"),
                                          title=f"Exponente de cizalladura α ({m.heights[0]:g}–{m.heights[-1]:g} m)"))
        st.download_button("Descargar serie horaria (CSV)", m.df.to_csv(index_label="time_utc"), "viento_horario.csv")

# ------------------------------------------------------------------ 3. terreno
with tabs[2]:
    st.header("Terreno y rugosidad")
    if SS.layout is None:
        st.info("Primero defina el layout (paso 1).")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            tsrc = st.selectbox("Fuente", ["Descargar Copernicus DEM 30 m + ESA WorldCover 10 m",
                                           "Subir GeoTIFF propio (p. ej. INEGI CEM 3.0)", "Terreno plano (sin efectos)"])
        with c2:
            buf = st.slider("Margen alrededor del parque [km]", 3, 20, 8,
                            help="Debe incluir colinas y cambios de rugosidad que afecten al parque.")
        with c3:
            res = st.select_slider("Resolución de cálculo [m]", [50, 100, 200], 100)
        with st.expander("Tabla de rugosidad por tipo de cobertura (editable)"):
            z0tab = pd.DataFrame({"Clase": list(terrain.WORLDCOVER_Z0),
                                  "Cobertura": [terrain.WORLDCOVER_NAMES[k] for k in terrain.WORLDCOVER_Z0],
                                  "z0 [m]": list(terrain.WORLDCOVER_Z0.values())})
            z0tab = st.data_editor(z0tab, disabled=["Clase", "Cobertura"], hide_index=True)
        up_dem = up_z0 = None
        if tsrc.startswith("Subir"):
            up_dem = st.file_uploader("GeoTIFF de elevación", type=["tif", "tiff"], key="dem_up")
            up_z0 = st.file_uploader("GeoTIFF de rugosidad z0 [m] (opcional; si no, se descarga WorldCover)",
                                     type=["tif", "tiff"], key="z0_up")
            st.caption("INEGI CEM 3.0: https://www.inegi.org.mx/app/geo2/elevacionesmex/ — descargue el estado/área y súbalo aquí.")
        if st.button("Preparar terreno", type="primary"):
            g, xs, ys = terrain.make_grid(SS.layout.lat, SS.layout.lon, buf, res)
            bar = st.progress(0.0, text="Preparando malla…")
            try:
                if tsrc.startswith("Terreno plano"):
                    terrain.flat_terrain(g)
                else:
                    if up_dem is not None:
                        p = Path(tempfile.gettempdir()) / "vientomx_dem_upload.tif"
                        p.write_bytes(up_dem.getvalue())
                        terrain.load_raster_to_grid(str(p), g, "elev")
                    else:
                        terrain.download_dem(g, progress=progress_cb(bar))
                    if up_z0 is not None:
                        p = Path(tempfile.gettempdir()) / "vientomx_z0_upload.tif"
                        p.write_bytes(up_z0.getvalue())
                        terrain.load_raster_to_grid(str(p), g, "z0")
                    else:
                        terrain.download_worldcover(g, progress=progress_cb(bar),
                                                    z0_table=dict(zip(z0tab["Clase"], z0tab["z0 [m]"])))
                SS.grid, SS.results = g, None
                bar.progress(1.0, text="Terreno listo")
            except Exception as e:
                st.error(f"Error al preparar el terreno: {e}")

    g = SS.grid
    if g is not None and g.elev is not None and SS.layout is not None:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, g.crs, always_xy=True)
        xs, ys = tr.transform(SS.layout.lon.to_numpy(), SS.layout.lat.to_numpy())
        xk, yk = (g.x - g.x0) / 1e3, (g.y - g.y0) / 1e3
        txk, tyk = (np.asarray(xs) - g.x0) / 1e3, (np.asarray(ys) - g.y0) / 1e3
        st.caption(f"Malla {g.nx}×{g.ny} a {g.dx:g} m en {g.crs.name}. Elevación: {g.meta.get('dem_source')}; "
                   f"rugosidad: {g.meta.get('z0_source')}.")

        def turb_trace():
            return go.Scatter(x=txk, y=tyk, mode="markers", marker=dict(color="white", size=8, line=dict(color="black", width=1.5)),
                              text=SS.layout.id, name="Turbinas")

        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure([go.Heatmap(z=g.elev, x=xk, y=yk, colorscale="earth", colorbar=dict(title="m")), turb_trace()])
            fig.update_layout(title="Elevación [m s.n.m.]", height=480, yaxis_scaleanchor="x", xaxis_title="km", yaxis_title="km")
            st.plotly_chart(fig)
        with c2:
            fig = go.Figure([go.Heatmap(z=np.log10(np.maximum(g.z0, 1e-4)), x=xk, y=yk, colorscale="YlGn",
                                        colorbar=dict(title="log10 z0", tickvals=[-4, -3, -2, -1, 0],
                                                      ticktext=["0.0001", "0.001", "0.01", "0.1", "1"])), turb_trace()])
            fig.update_layout(title="Rugosidad z0 [m]", height=480, yaxis_scaleanchor="x", xaxis_title="km", yaxis_title="km")
            st.plotly_chart(fig)
        c1, c2 = st.columns([2, 1])
        with c1:
            wd_sel = st.slider("Dirección del viento para el mapa de aceleración [°]", 0, 330, 0, 30)
            du = terrain.orographic_speedup_map(g.elev, g.dx, wd_sel, float(SS.hub_height))
            fig = go.Figure([go.Heatmap(z=100 * du, x=xk, y=yk, colorscale="RdBu_r", zmid=0, colorbar=dict(title="%")), turb_trace()])
            fig.update_layout(title=f"Aceleración orográfica a {SS.hub_height:g} m, viento de {wd_sel}°", height=480,
                              yaxis_scaleanchor="x", xaxis_title="km", yaxis_title="km")
            st.plotly_chart(fig)
        with c2:
            rx = terrain.rix(g, xs, ys)
            elev_t = g.sample(g.elev, xs, ys)
            st.dataframe(pd.DataFrame({"ID": SS.layout.id, "Elevación [m]": elev_t.round(0), "RIX [%]": rx.round(1)}),
                         hide_index=True, height=420)
            if rx.max() > 5:
                st.warning("Hay turbinas con RIX > 5 %: el modelo lineal tiende a sobrestimar la aceleración en terreno "
                           "escarpado. Se agrega incertidumbre por complejidad del terreno.")
            if g.landcover is not None:
                vals, cnt = np.unique(g.landcover, return_counts=True)
                lc = pd.DataFrame({"Cobertura": [terrain.WORLDCOVER_NAMES.get(int(v), str(v)) for v in vals],
                                   "%": 100 * cnt / cnt.sum()})
                st.plotly_chart(px.bar(lc, x="%", y="Cobertura", orientation="h", height=260, title="Cobertura de suelo"))

# ------------------------------------------------------------------ 4. turbina
with tabs[3]:
    st.header("Aerogenerador")
    lib = get_library()
    tab = turbines.library_table(lib)
    c1, c2 = st.columns([1, 1])
    with c1:
        q = st.text_input("Buscar modelo (p. ej. Vestas, V90, Siemens, 3.4 MW)")
        pr = st.slider("Potencia nominal [MW]", 0.0, 16.0, (1.0, 16.0), 0.1)
        filt = tab[(tab["Potencia [kW]"] >= pr[0] * 1000) & (tab["Potencia [kW]"] <= pr[1] * 1000)]
        if q:
            filt = filt[filt["Modelo"].str.contains(q, case=False, regex=False)]
        st.dataframe(filt, hide_index=True, height=300)
        names = filt["Modelo"].tolist()
        cur = SS.turbine.name if SS.turbine is not None and SS.turbine.name in names else None
        sel = st.selectbox(f"Seleccionar ({len(names)} modelos)", names, index=names.index(cur) if cur else 0) if names else None
        if sel and st.button("Usar este modelo", type="primary"):
            SS.turbine, SS.results = lib[sel], None
            if lib[sel].hub_heights:
                SS.hub_height = float(lib[sel].hub_heights[0])
        with st.expander("Agregar curva propia (CSV de ficha técnica)"):
            upt = st.file_uploader("CSV con columnas: velocidad [m/s], potencia [kW], Ct (opcional)", type=["csv"])
            nm = st.text_input("Nombre", "Mi turbina")
            dd = st.number_input("Diámetro [m]", 10.0, 300.0, 150.0)
            if upt is not None and st.button("Cargar curva propia"):
                try:
                    SS.turbine, SS.results = turbines.load_turbine_csv(upt, nm, dd), None
                    st.success("Curva cargada.")
                except Exception as e:
                    st.error(str(e))
        SS.hub_height = st.number_input("Altura de buje [m]", 20.0, 250.0, float(SS.hub_height), 1.0)
    with c2:
        t = SS.turbine
        if t is not None:
            st.subheader(t.name)
            st.caption(f"{t.rated_kw:,.0f} kW · rotor {t.rotor_diameter:g} m · arranque {t.cut_in:g} m/s · corte {t.cut_out:g} m/s · "
                       f"fuente: {t.source}")
            u = np.linspace(0, 30, 301)
            rho_site = None
            if SS.met is not None:
                rho_site = climate.air_density(SS.met, None, SS.hub_height)
            fig = go.Figure([go.Scatter(x=u, y=t.power(u), name="P (ρ=1.225)", line=dict(width=3))])
            if rho_site:
                fig.add_trace(go.Scatter(x=u, y=t.power(u, rho_site), name=f"P (ρ sitio≈{rho_site:.3f})", line=dict(dash="dash")))
            fig.add_trace(go.Scatter(x=u, y=t.thrust_coefficient(u), name="Ct" + (" (estimado)" if t.ct_estimated else ""),
                                     yaxis="y2", line=dict(color="#e45756")))
            fig.update_layout(xaxis_title="Velocidad [m/s]", yaxis_title="Potencia [kW]", height=420,
                              yaxis2=dict(title="Ct [-]", overlaying="y", side="right", range=[0, 1.1]),
                              legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig)
            st.download_button("Descargar curva (CSV)", t.to_frame().to_csv(index=False), "curva_potencia.csv")
            if t.ct_estimated:
                st.info("Esta curva no incluye Ct; se estimó con teoría de disco actuador calibrada con turbinas de "
                        "referencia. Para estudios formales use la curva Ct del fabricante.")
        else:
            st.info("Seleccione un modelo de la librería.")

# ------------------------------------------------------------------ 5. simulación
with tabs[4]:
    st.header("Simulación de producción anual")
    missing = [n for n, v in [("layout", SS.layout), ("datos de viento", SS.met), ("aerogenerador", SS.turbine)] if v is None]
    if missing:
        st.info("Falta: " + ", ".join(missing) + ". El terreno es opcional (si falta, se supone plano).")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Estelas")
        wm = st.selectbox("Modelo", ["Jensen / Park (WAsP clásico)", "Gaussiano Bastankhah", "Sin estelas"])
        wmk = {"J": "jensen", "G": "bastankhah", "S": "none"}[wm[0]]
        k = st.number_input("Constante de expansión k (Jensen)", 0.02, 0.15, 0.075, 0.005,
                            help="0.075 en tierra, 0.04–0.05 en mar o terreno muy liso.")
        ti = st.number_input("Intensidad de turbulencia ambiente (Gaussiano)", 0.03, 0.30, 0.10, 0.01)
    with c2:
        st.subheader("Flujo y densidad")
        use_t = st.checkbox("Efectos de orografía", True)
        use_r = st.checkbox("Efectos de rugosidad", True)
        hb = st.number_input("Altura de mezcla para rugosidad [m]", 100.0, 1000.0, 300.0, 50.0)
        rho_auto = st.checkbox("Densidad del aire de los datos", True)
        rho_man = st.number_input("Densidad manual [kg/m³]", 0.8, 1.3, 1.225, 0.005, disabled=rho_auto)
    with c3:
        st.subheader("Pérdidas [%]")
        ldf = pd.DataFrame({"Concepto": list(aep.DEFAULT_LOSSES), "Pérdida [%]": list(aep.DEFAULT_LOSSES.values())})
        ldf = st.data_editor(ldf, hide_index=True, disabled=["Concepto"], key="loss_ed")
    with st.expander("Incertidumbres (1σ)"):
        st.caption("'ws' = % de velocidad (se convierte a energía con la sensibilidad calculada); 'energy' = % de energía; "
                   "'wake_frac' = % de la pérdida por estelas. Con medición en sitio + MCP, reduzca 'Datos de viento' a ~2–3 %.")
        default_unc = dict(aep.DEFAULT_UNCERTAINTY)
        if SS.met is not None and "Medición" in SS.met.source or (SS.met is not None and "MCP" in SS.met.source):
            default_unc["Datos de viento (fuente)"] = ("ws", 2.5)
        elif SS.calib:
            default_unc["Datos de viento (fuente)"] = ("ws", 5.0)
        udf = pd.DataFrame({"Componente": list(default_unc), "Tipo": [v[0] for v in default_unc.values()],
                            "Valor": [v[1] for v in default_unc.values()]})
        udf = st.data_editor(udf, hide_index=True, disabled=["Componente", "Tipo"], key="unc_ed")

    if st.button("▶ Calcular AEP", type="primary", disabled=bool(missing)):
        cfg = aep.SimConfig(hub_height=float(SS.hub_height), wake_model=wmk, wake_k=k, ti=ti, use_terrain=use_t,
                            use_roughness=use_r, blending_height=hb,
                            losses=dict(zip(ldf["Concepto"], ldf["Pérdida [%]"])),
                            uncertainty={r.Componente: (r.Tipo, float(r.Valor)) for r in udf.itertuples()})
        g = SS.grid
        if g is None:
            g, _, _ = terrain.make_grid(SS.layout.lat, SS.layout.lon, 5, 100)
            terrain.flat_terrain(g)
            SS.grid = g
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, g.crs, always_xy=True)
        _x, _y = tr.transform(SS.layout.lon.to_numpy(), SS.layout.lat.to_numpy())
        site_elev = float(np.mean(g.sample(g.elev, _x, _y)))
        rho = climate.air_density(SS.met, site_elev, cfg.hub_height) if rho_auto else rho_man
        ts = climate.extrapolate_to_height(SS.met, cfg.hub_height)
        ref = SS.ref_latlon
        if SS.calib:
            ts, _ = climate.calibrate_series(SS.met, ts, SS.calib, SS.calib_h or cfg.hub_height)
            ref = ref or SS.calib_ref
        bar = st.progress(0.0, text="Iniciando…")
        try:
            SS.results = aep.run_aep(SS.layout, ts, SS.turbine, cfg, g, rho, ref, progress=progress_cb(bar))
            SS.cfg = cfg
            st.success("Cálculo terminado. Vea la pestaña ⑥ Resultados.")
        except Exception as e:
            st.exception(e)

# ------------------------------------------------------------------ 6. resultados
with tabs[5]:
    st.header("Resultados")
    r = SS.results
    if r is None:
        st.info("Ejecute la simulación (paso 5).")
    else:
        s = r.summary
        for w in aep.reliability_warnings(s, SS.met.source if SS.met is not None else "", bool(SS.calib)):
            st.warning(w)
        k = st.columns(6)
        k[0].metric("Capacidad", f"{s['Capacidad instalada [MW]']:.1f} MW")
        k[1].metric("AEP neta P50", f"{s['AEP neta P50 [GWh]']:.1f} GWh")
        k[2].metric("AEP P90 (10 años)", f"{s['AEP P90 (10 años) [GWh]']:.1f} GWh")
        k[3].metric("Factor de planta", f"{s['Factor de planta neto [%]']:.1f} %")
        k[4].metric("Pérdida por estelas", f"{s['Pérdida por estelas [%]']:.1f} %")
        k[5].metric("Velocidad libre media", f"{s['Velocidad media libre en turbinas [m/s]']:.2f} m/s")

        c1, c2 = st.columns([3, 2])
        with c1:
            color = st.radio("Colorear turbinas por", ["AEP neta P50 [MWh]", "Pérdida por estelas [%]", "U libre [m/s]",
                                                        "Factor terreno"], horizontal=True)
            (clat, clon), zm = layout.map_view(r.per_turbine["Latitud"], r.per_turbine["Longitud"], height_px=450)
            fig = px.scatter_map(r.per_turbine, lat="Latitud", lon="Longitud", color=color, hover_name="ID",
                                 hover_data=["AEP neta P50 [MWh]", "Pérdida por estelas [%]", "U libre [m/s]"],
                                 color_continuous_scale="Viridis", zoom=zm, center=dict(lat=clat, lon=clon),
                                 height=450, map_style="open-street-map")
            fig.update_traces(marker=dict(size=13))
            fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), uirevision=f"res-{SS.layout_ver}")
            st.plotly_chart(fig)
        with c2:
            st.dataframe(pd.DataFrame(list(s.items()), columns=["Concepto", "Valor"]).assign(
                Valor=lambda d: d.Valor.map(lambda v: f"{v:,.2f}" if isinstance(v, (float, np.floating)) else str(v))),
                hide_index=True, height=450)

        st.subheader("Resultados por aerogenerador")
        st.dataframe(r.per_turbine, hide_index=True)

        st.subheader("Mapa de estelas")
        c1, c2 = st.columns([1, 3])
        with c1:
            wd_w = st.slider("Dirección [°]", 0, 350, int(r.sector.loc[r.sector["Frecuencia [%]"].idxmax(), "Sector [°]"]), 10)
            u_w = st.slider("Velocidad de referencia [m/s]", 3.0, 20.0, 9.0, 0.5)
        with c2:
            x, y, Uw = aep.wake_map(r, SS.grid, SS.turbine, SS.cfg, wd_w, u_w)
            fig = go.Figure([go.Heatmap(z=Uw, x=(x - x.min()) / 1e3, y=(y - y.min()) / 1e3, colorscale="Turbo",
                                        colorbar=dict(title="m/s")),
                             go.Scatter(x=(r.xs - x.min()) / 1e3, y=(r.ys - y.min()) / 1e3, mode="markers",
                                        marker=dict(color="white", size=7, line=dict(color="black", width=1)), text=r.per_turbine.ID)])
            fig.update_layout(height=500, yaxis_scaleanchor="x", xaxis_title="km", yaxis_title="km",
                              title=f"Velocidad a altura de buje, viento de {wd_w}° a {u_w} m/s")
            st.plotly_chart(fig)

        c1, c2 = st.columns(2)
        with c1:
            md = r.monthly_diurnal
            st.plotly_chart(px.imshow(md, aspect="auto", color_continuous_scale="Viridis",
                                      labels=dict(x="Hora local", y="Mes", color="FP %"),
                                      title="Factor de planta neto 12×24 (útil para análisis de mercado CENACE)"))
        with c2:
            st.plotly_chart(px.bar(r.annual.reset_index(), x="index", y="Energía neta [GWh]",
                                   labels={"index": "Año"}, title="Producción neta por año (variabilidad interanual)")
                            .add_hline(y=s["AEP neta P50 [GWh]"], line_dash="dash", annotation_text="P50"))
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure(go.Barpolar(r=r.sector["Energía bruta [%]"], theta=r.sector["Sector [°]"], name="Energía",
                                        marker_color="#4c78a8"))
            fig.update_layout(title="Rosa de energía (%)", polar=dict(angularaxis=dict(direction="clockwise", rotation=90)))
            st.plotly_chart(fig)
        with c2:
            sig = s["Incertidumbre total, 10 años [%]"] / 100
            from scipy.stats import norm
            p = np.linspace(1, 99, 99)
            e = s["AEP neta P50 [GWh]"] * (1 + norm.ppf(1 - p / 100) * sig)
            sig1 = s["Incertidumbre total, 1 año [%]"] / 100
            e1 = s["AEP neta P50 [GWh]"] * (1 + norm.ppf(1 - p / 100) * sig1)
            fig = go.Figure([go.Scatter(x=p, y=e, name="10 años"), go.Scatter(x=p, y=e1, name="1 año", line=dict(dash="dash"))])
            fig.update_layout(title="Curva de excedencia", xaxis_title="Probabilidad de excedencia Pxx [%]",
                              yaxis_title="GWh/año")
            st.plotly_chart(fig)
        with st.expander("Pérdidas e incertidumbre"):
            c1, c2 = st.columns(2)
            c1.dataframe(r.losses.round(2), hide_index=True)
            c2.dataframe(r.uncertainty.round(2), hide_index=True)
        inputs = {"Fuente de viento": SS.met.source, "Punto de referencia": f"{SS.met.lat}, {SS.met.lon}",
                  "Calibración (m/s)": f"{SS.calib:.2f} a {SS.calib_h or SS.cfg.hub_height:g} m" if SS.calib else "no", "Terreno": SS.grid.meta.get("dem_source") if SS.grid else "plano",
                  "Rugosidad": SS.grid.meta.get("z0_source") if SS.grid else "-", "Modelo de estelas": SS.cfg.wake_model,
                  "k": SS.cfg.wake_k, "TI": SS.cfg.ti, "Altura de buje": SS.cfg.hub_height}
        c1, c2 = st.columns(2)
        c1.download_button("⬇ Reporte Excel", report.results_to_excel(r, inputs), "VientoMX_resultados.xlsx", type="primary")
        c2.download_button("⬇ Serie horaria de producción (CSV)", report.hourly_to_csv(r), "VientoMX_produccion_horaria.csv")

        st.subheader("Reporte PDF del estudio")
        st.caption("Incluye todas las etapas (sitio, viento, terreno, aerogenerador, simulación) con su configuración, "
                   "supuestos y advertencias, y los resultados con tablas y gráficas.")
        c1, c2 = st.columns(2)
        with c1:
            pdf_proj = st.text_input("Nombre del proyecto", SS.get("pdf_project", ""), key="pdf_project")
            pdf_author = st.text_input("Elaboró", SS.get("pdf_author", ""), key="pdf_author")
        with c2:
            pdf_notes = st.text_area("Notas del analista (opcional)", SS.get("pdf_notes", ""), key="pdf_notes",
                                     height=110)
        pdf_key = (id(r), pdf_proj, pdf_author, pdf_notes)
        if st.button("Generar reporte PDF"):
            with st.spinner("Generando PDF (gráficas y tablas)…"):
                try:
                    study = pdf_report.Study(
                        layout=SS.layout, met=SS.met, turbine=SS.turbine, hub_height=float(SS.cfg.hub_height),
                        grid=SS.grid, cfg=SS.cfg, results=r, calib=SS.calib, calib_h=SS.calib_h,
                        calib_ref=SS.calib_ref, gwa_info=SS.gwa_info, mcp_info=SS.mcp_info, ref_latlon=SS.ref_latlon,
                        project=pdf_proj, author=pdf_author, notes=pdf_notes)
                    SS.pdf_bytes, SS.pdf_key = pdf_report.build_pdf(study), pdf_key
                except Exception as e:
                    st.error(f"No se pudo generar el PDF: {e}")
        if SS.get("pdf_bytes") and SS.get("pdf_key") == pdf_key:
            st.download_button("⬇ Descargar reporte PDF", SS.pdf_bytes, "VientoMX_reporte.pdf", "application/pdf",
                               type="primary")
        elif SS.get("pdf_bytes"):
            st.caption("Los resultados o los datos del reporte cambiaron: vuelva a generar el PDF.")

# ------------------------------------------------------------------ método
with tabs[6]:
    st.markdown((Path(__file__).resolve().parents[1] / "docs" / "metodo.md").read_text(encoding="utf-8"))
