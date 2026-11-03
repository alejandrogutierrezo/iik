import io
import json

import numpy as np
import pandas as pd
import pytest

from vientomx import aep, climate, layout, met, terrain, turbines, wake


@pytest.fixture(scope="module")
def lib():
    return turbines.load_library()


def test_library_has_commercial_and_reference(lib):
    assert len(lib) > 100
    assert any("Vestas" in k for k in lib)
    t = lib["IEA Reference 3.4 MW D130"]
    assert not t.ct_estimated
    assert t.rated_kw == pytest.approx(3400, rel=0.01)


def test_ct_estimate_close_to_reference(lib):
    t = lib["NREL Reference 5 MW D126"]
    est = turbines.estimate_ct(t.ws, t.power_kw, t.rotor_diameter)
    m = (t.ws >= 5) & (t.ws <= 20)
    assert np.mean(np.abs(est[m] - t.ct[m])) < 0.08


def test_density_correction(lib):
    t = lib["IEA Reference 3.4 MW D130"]
    assert t.power(8, 1.0) < t.power(8, 1.225)
    assert t.power(26) == 0


def test_jensen_analytic():
    # Ct=0.8, x=7D, k=0.075 -> (1-sqrt(0.2))/(1+0.075*14)^2
    d = wake.jensen_deficit(0.8, 7 * 100, 0.0, 100, 0.075)
    assert d == pytest.approx((1 - np.sqrt(0.2)) / (1 + 0.075 * 14) ** 2, rel=1e-6)
    assert wake.jensen_deficit(0.8, -500, 0.0, 100) == 0


def test_farm_speeds_downstream_only(lib):
    t = lib["IEA Reference 3.4 MW D130"]
    xs, ys = np.array([0.0, 0.0]), np.array([0.0, -910.0])  # 2a turbina al sur
    u = np.full((2, 1), 9.0)
    ue = wake.farm_speeds(xs, ys, 0.0, u, t)  # viento del norte
    assert ue[0, 0] == 9.0 and ue[1, 0] < 9.0
    ue2 = wake.farm_speeds(xs, ys, 90.0, u, t)  # viento del este: sin estela
    assert np.allclose(ue2, 9.0)


def test_orographic_agnesi():
    dx, n, H, L = 50, 512, 100, 1000
    x = (np.arange(n) - n / 2) * dx
    elev = np.tile(H * L**2 / (x**2 + L**2), (n, 1))
    for z in (10, 100):
        du = terrain.orographic_speedup_map(elev, dx, 270, z, cutoff_wavelength=None, taper=0.0)
        assert du[n // 2, n // 2] == pytest.approx(H * L / (L + z) ** 2, rel=0.03)


def test_roughness_factor_direction():
    f = terrain.roughness_factors(np.array([0.3]), 0.03, 100)
    assert f[0] < 1
    assert terrain.roughness_factors(np.array([0.03]), 0.03, 100)[0] == pytest.approx(1)


def test_tile_names():
    assert terrain.copernicus_tile_url(16, -95).endswith("Copernicus_DSM_COG_10_N16_00_W095_00_DEM.tif")
    assert terrain.worldcover_tile_url(15, -96).endswith("_N15W096_Map.tif")


def test_layout_formats():
    csv = "Nombre;Latitud;Longitud\nA;16.5;-94.9\nB;16.51;-94.9\n"
    df = layout.load_layout(io.BytesIO(csv.encode()), "x.csv")
    assert list(df.id) == ["A", "B"]
    utm = "id,Este,Norte\nA,290547,1829873\n"
    df = layout.load_layout(io.BytesIO(utm.encode()), "x.csv", utm_zone=15)
    assert df.lat[0] == pytest.approx(16.5417, abs=1e-3)
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>T1</name><Point><coordinates>-94.9,16.5,0</coordinates></Point></Placemark>
      </Document></kml>"""
    df = layout.load_layout(io.BytesIO(kml), "x.kml")
    assert df.lon[0] == -94.9


def test_parsers():
    om = {"hourly_units": {"wind_speed_100m": "m/s"},
          "hourly": {"time": ["2020-01-01T00:00", "2020-01-01T01:00"], "wind_speed_10m": [5, 6],
                     "wind_speed_100m": [8, 9], "wind_direction_10m": [350, 0], "wind_direction_100m": [355, 5],
                     "temperature_2m": [25, 24], "surface_pressure": [1000, 1001]}}
    df = met.parse_open_meteo(om)
    assert list(df.columns[:4]) == ["ws_10", "wd_10", "ws_100", "wd_100"] and df.ws_100.iloc[1] == 9
    nasa = {"geometry": {"coordinates": [-94.9, 16.5, 40.0]},
            "properties": {"parameter": {"WS50M": {"2020010100": 7.0, "2020010101": -999.0},
                                          "WD50M": {"2020010100": 10.0, "2020010101": 20.0},
                                          "PS": {"2020010100": 100.5, "2020010101": 100.4}}}}
    df, elev = met.parse_nasa_power(nasa)
    assert elev == 40 and np.isnan(df.ws_50.iloc[1]) and df.ps.iloc[0] == pytest.approx(1005)


def test_mcp_recovers_linear_relation():
    m = met.synthetic_met(years=(2015, 2017))
    ref = climate.extrapolate_to_height(m, 100)
    meas = ref.loc["2016"].copy()
    meas["ws"] = 1.1 * meas["ws"] + 0.3
    lt, info = climate.mcp_variance_ratio(meas, ref)
    assert lt.ws.mean() == pytest.approx(1.1 * ref.ws.mean() + 0.3, rel=0.02)


def test_end_to_end_flat(lib):
    t = lib["IEA Reference 3.4 MW D130"]
    m = met.synthetic_met(years=(2015, 2017))
    ts = climate.extrapolate_to_height(m, 110)
    lay = layout.grid_layout(16.55, -94.95, 2, 4, 7, 3, 130)
    g, _, _ = terrain.make_grid(lay.lat, lay.lon, 4, 100)
    terrain.flat_terrain(g)
    cfg = aep.SimConfig(hub_height=110)
    r = aep.run_aep(lay, ts, t, cfg, g, 1.2)
    s = r.summary
    assert np.allclose(r.flow_factors, 1)
    # bruta = curva de potencia sobre la serie
    gross = t.power(ts.ws.to_numpy(), 1.2).mean() * 8766 / 1e6 * 8
    assert s["AEP bruta [GWh]"] == pytest.approx(gross, rel=0.01)
    assert 0 < s["Pérdida por estelas [%]"] < 20
    assert s["AEP P90 (10 años) [GWh]"] < s["AEP neta P50 [GWh]"]
    cfg0 = aep.SimConfig(hub_height=110, wake_model="none")
    assert aep.run_aep(lay, ts, t, cfg0, g, 1.2).summary["Pérdida por estelas [%]"] == pytest.approx(0, abs=1e-9)


def test_air_density_altiplano():
    m = met.MetData(pd.DataFrame({"ws_100": [8.0]}, index=pd.DatetimeIndex(["2020-01-01"], tz="UTC")), 23, -102, "x")
    rho = climate.air_density(m, 2300, 100)
    assert 0.9 < rho < 1.0


def test_osm_parse_and_layout_editing():
    from unittest import mock

    from vientomx import osm

    data = {"elements": [
        {"type": "node", "id": 101, "lat": 25.6101, "lon": -100.6801,
         "tags": {"power": "generator", "generator:source": "wind", "ref": "SC-01", "manufacturer": "Gamesa",
                  "model": "G90", "generator:output:electricity": "2 MW", "height:hub": "78 m",
                  "rotor:diameter": "90"}},
        {"type": "node", "id": 102, "lat": 25.6050, "lon": -100.6790,
         "tags": {"power": "generator", "generator:source": "wind", "generator:output:electricity": "2000 kW"}},
        {"type": "way", "id": 7, "center": {"lat": 25.60, "lon": -100.678},
         "tags": {"power": "generator", "generator:source": "wind"}},
        {"type": "node", "id": 101, "lat": 25.6101, "lon": -100.6801, "tags": {}},  # duplicado
    ]}
    df = osm.parse_overpass(data)
    assert len(df) == 3 and df.osm_id.iloc[0] == "n101"
    assert df.potencia_kw.tolist()[:2] == [2000.0, 2000.0]
    assert df.altura_buje_m.iloc[0] == 78 and df.diametro_rotor_m.iloc[0] == 90
    lay = osm.to_layout(df)
    assert lay["id"].tolist()[0] == "SC-01" and lay["id"].nunique() == 3 and "osm_id" in lay

    q = osm.build_query(25.5, -100.8, 25.7, -100.6)
    assert '"generator:source"="wind"' in q and "(25.500000,-100.800000,25.700000,-100.600000)" in q
    with pytest.raises(ValueError):
        osm.fetch_wind_turbines(20, -110, 25, -100)  # área demasiado grande

    resp = mock.Mock(json=lambda: data, raise_for_status=lambda: None)
    with mock.patch("vientomx.osm.requests.post", side_effect=[OSError("bloqueado"), resp]) as post:
        got = osm.fetch_wind_turbines(25.59, -100.69, 25.62, -100.67)
    assert len(got) == 3 and post.call_count == 2  # se usa el servidor espejo

    # edición manual del layout
    L = layout.add_turbine(None, 25.61, -100.68)
    L = layout.add_turbine(L, 25.62, -100.68)
    L = layout.add_turbine(L, 25.63, -100.68, "WTG01")  # id repetido -> se renombra
    assert L["id"].tolist() == ["WTG01", "WTG02", "WTG03"]
    i, d = layout.nearest(L, 25.6201, -100.68)
    assert i == 1 and d == pytest.approx(11.1, abs=0.2)
    L = layout.move_turbine(L, i, 25.625, -100.681)
    assert L.loc[1, "lat"] == 25.625
    L = layout.remove_turbine(L, 0)
    assert L["id"].tolist() == ["WTG02", "WTG03"]
    assert layout.distance_m(0, 0, 1, 0) == pytest.approx(111195, rel=1e-3)
    # clic en otra "copia" del mundo del mapa web: -460.64° == -100.64°
    assert layout.wrap_lon(-460.6405) == pytest.approx(-100.6405)
    assert layout.wrap_lon(259.36) == pytest.approx(-100.64)
    W = layout.add_turbine(None, 25.68, -460.6405)
    assert W.lon.iloc[0] == pytest.approx(-100.6405)
    assert layout.move_turbine(W, 0, 25.69, 619.36).lon.iloc[0] == pytest.approx(-100.64)


def test_download_errors_are_explained():
    from unittest import mock

    def resp(status, body, ctype):
        r = mock.Mock(status_code=status, ok=200 <= status < 300, text=body, headers={"Content-Type": ctype})
        r.json.side_effect = (lambda: __import__("json").loads(body))
        return r

    html = resp(200, "<html><head><title>Acceso bloqueado</title></head><body>Firewall de la red</body></html>",
                "text/html")
    ok = resp(200, '{"elevation": 700, "hourly": {"time": ["2005-01-01T00:00"], "wind_speed_10m": [3.0], '
                   '"wind_direction_10m": [90], "wind_speed_100m": [6.0], "wind_direction_100m": [95]}}',
              "application/json")
    with mock.patch("vientomx.met.time.sleep"), mock.patch("vientomx.met.requests.get", side_effect=[html, ok]):
        m = met.download_open_meteo_era5(25.68, -100.64, 2005, 2005, use_cache=False)
    assert m.df["ws_100"].iloc[0] == 6.0  # se recupera al reintentar

    with mock.patch("vientomx.met.time.sleep"), mock.patch("vientomx.met.requests.get", return_value=html):
        with pytest.raises(met.DownloadError) as e:
            met.download_open_meteo_era5(25.68, -100.64, 2005, 2005, use_cache=False)
    msg = str(e.value)
    assert "text/html" in msg and "Firewall de la red" in msg and "archive-api.open-meteo.com" in msg

    bad = resp(400, '{"error": true, "reason": "Parameter models is invalid"}', "application/json")
    with mock.patch("vientomx.met.time.sleep"), mock.patch("vientomx.met.requests.get", return_value=bad) as g:
        with pytest.raises(met.DownloadError, match="models is invalid"):
            met.download_open_meteo_era5(25.68, -100.64, 2005, 2005, use_cache=False)
    assert g.call_count == 1  # un error de parámetros no se reintenta


def _fake_wtk_file(path, n_sites=50, nt=8760, extra=0.0):
    import h5py

    rng = np.random.default_rng(1)
    lat = 25.6 + (np.arange(n_sites) // 10) * 0.018
    lon = -100.7 + (np.arange(n_sites) % 10) * 0.02
    with h5py.File(path, "w") as h:
        h["coordinates"] = np.column_stack([lat, lon]).astype("f4")
        meta = np.zeros(n_sites, dtype=[("latitude", "<f4"), ("longitude", "<f4"), ("county", "S38"),
                                         ("elevation", "<i2")])
        meta["latitude"], meta["longitude"], meta["county"] = lat, lon, b"Santa Catarina"
        meta["elevation"] = 1200 + np.arange(n_sites) * 10
        h["meta"] = meta
        h["time_index"] = np.array(pd.date_range("2010-01-01", periods=nt, freq="h").strftime("%Y-%m-%d %H:%M:%S"),
                                   dtype="S20")
        base = 6 + extra + 2 * np.sin(np.arange(nt) / 24 * 2 * np.pi)[:, None] + 0.01 * np.arange(n_sites)[None, :]
        for hh, fac in ((80, 0.97), (100, 1.0)):
            d = h.create_dataset(f"windspeed_{hh}m", data=np.round(base * fac * 100).astype("<u2"), chunks=(1344, 16))
            d.attrs["scale_factor"], d.attrs["fill_value"] = 100.0, 65535
            d = h.create_dataset(f"winddirection_{hh}m", data=np.full((nt, n_sites), 9000, "<u2"), chunks=(1344, 16))
            d.attrs["scale_factor"], d.attrs["fill_value"] = 100.0, 65535
        d = h.create_dataset("temperature_2m", data=np.full((nt, n_sites), 1500, "<i2"), chunks=(1344, 16))
        d.attrs["scale_factor"], d.attrs["fill_value"] = 100.0, 32767
        d = h.create_dataset("pressure_0m", data=np.full((nt, n_sites), 8700, "<u2"), chunks=(1344, 16))
        d.attrs["scale_factor"], d.attrs["fill_value"] = 0.1, 65535


def test_wtk_reader(tmp_path, monkeypatch):
    from vientomx import wtk

    monkeypatch.setattr(wtk, "CACHE_DIR", tmp_path / "cache")
    f = tmp_path / "wtk_2010.h5"
    _fake_wtk_file(f)
    pts = wtk.nearest_points(25.618, -100.62, 4, src=str(f))
    assert pts.gid.iloc[0] == 14 and pts.dist_km.iloc[0] < 0.1 and pts.elev_m.iloc[0] == 1340
    assert wtk.heights_for_hub(85) == (80, 100) and wtk.heights_for_hub(100) == (80, 100)
    m = wtk.download_wtk_mexico(25.618, -100.62, 2010, 2010, hub_height=85, gid=17, src_for_year=lambda y: str(f))
    assert m.heights == [80, 100] and len(m.df) == 8760
    assert m.df["ws_100"].mean() == pytest.approx(6 + 0.17, abs=0.01)
    assert m.df["wd_100"].iloc[0] == 90 and m.df["t2m"].iloc[0] == 15 and m.df["ps"].iloc[0] == pytest.approx(870)
    assert m.elevation == pytest.approx(1267, abs=5) and "WIND Toolkit" in m.source  # altura de p = 870 hPa
    hub = climate.extrapolate_to_height(m, 85)
    assert m.df.ws_80.mean() < hub.ws.mean() < m.df.ws_100.mean()
    assert 0.9 < climate.air_density(m, 1300, 85) < 1.1
    cmp_ = wtk.compare_points([0, 17, 33], 2010, 100, src=str(f))
    assert cmp_.iloc[:, 1].tolist() == pytest.approx([6.0, 6.17, 6.33], abs=0.01)
    # segunda llamada: desde caché, sin abrir el archivo
    m2 = wtk.download_wtk_mexico(25.6, -100.6, 2010, 2010, 85, gid=17, src_for_year=lambda y: "/no/existe.h5")
    assert m2.df["ws_80"].equals(m.df["ws_80"])
    with pytest.raises(ValueError):
        wtk.download_wtk_mexico(25.6, -100.6, 2005, 2006, 85, gid=17)

    # caché de la versión anterior: el archivo "_tp" del primer año traía también las columnas de viento
    old = m.df.copy()
    old.to_csv(tmp_path / "cache" / "wtk_mexico_23_2010_80_100_tp.csv.gz")
    m5 = wtk.download_wtk_mexico(25.6, -100.6, 2010, 2010, 85, gid=23, src_for_year=lambda y: str(f))
    assert sorted(m5.df.columns) == ["ps", "t2m", "wd_100", "ws_100", "ws_80"]
    assert m5.df["ws_100"].mean() == pytest.approx(6.23, abs=0.01)

    # el índice de chunks del año de referencia (2007) se reutiliza en los demás años sin buscar en el B-tree
    f07, f09 = tmp_path / "wtk_2007.h5", tmp_path / "wtk_2009.h5"
    _fake_wtk_file(f07)
    _fake_wtk_file(f09, extra=1.0)
    files = {2007: str(f07), 2009: str(f09)}
    calls = []
    orig = wtk._column_offsets
    monkeypatch.setattr(wtk, "_column_offsets", lambda *a, **k: calls.append(1) or orig(*a, **k))
    m3 = wtk.download_wtk_mexico(25.6, -100.6, 2009, 2009, 85, gid=5, src_for_year=files.get)
    assert m3.df["ws_100"].mean() == pytest.approx(7.05, abs=0.01)  # datos de 2009, índice de 2007
    n_first = len(calls)
    m4 = wtk.download_wtk_mexico(25.6, -100.6, 2009, 2009, 85, gid=6, src_for_year=files.get, use_cache=False)
    assert len(calls) == n_first  # misma columna de chunks: sin búsquedas nuevas
    assert m4.df["ws_100"].mean() == pytest.approx(7.06, abs=0.01)


def test_gwa_sampling_and_calibration(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin

    from vientomx import gwa

    data = np.full((40, 40), 7.5, "f4")
    data[:20, :] = 6.0  # mitad norte más lenta
    p = tmp_path / "gwa.tif"
    with rasterio.open(p, "w", driver="GTiff", width=40, height=40, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-100.75, 25.75, 0.0025, 0.0025), nodata=-9999) as ds:
        ds.write(data, 1)
    v = gwa.sample_raster(str(p), [25.74, 25.68, 30.0], [-100.70, -100.70, -100.70])
    assert v[0] == pytest.approx(6.0) and v[1] == pytest.approx(7.5) and np.isnan(v[2])
    assert gwa.sample_raster(p.read_bytes(), [25.68], [-100.7])[0] == pytest.approx(7.5)  # archivo subido
    assert gwa.nearest_height(85) == 100 and gwa.nearest_height(60) == 50
    # caché local del GeoTIFF nacional -> no se toca la red
    monkeypatch.setattr(gwa, "CACHE_DIR", tmp_path)
    (tmp_path / "gwa_mex_ws100.tif").write_bytes(p.read_bytes())
    assert gwa.fetch_mean_speed([25.68], [-100.7], 100)[0] == pytest.approx(7.5)

    m = met.synthetic_met(seed=3)
    hub = climate.extrapolate_to_height(m, 110)
    cal, f = climate.calibrate_series(m, hub, 7.0, 100)
    assert climate.extrapolate_to_height(m, 100).ws.mean() * f == pytest.approx(7.0)
    assert cal.ws.mean() == pytest.approx(hub.ws.mean() * f)
    assert climate.is_coarse_source("ERA5 (Open-Meteo, era5)") and not climate.is_coarse_source("NREL WIND Toolkit")
    assert not climate.is_coarse_source("MCP medición + ERA5")
    w = aep.reliability_warnings({"RIX medio [%]": 21, "Factor de planta neto [%]": 4.7}, "ERA5 (Open-Meteo)", False)
    assert len(w) == 2 and "subestimada" in w[0]
    assert aep.reliability_warnings({"RIX medio [%]": 1, "Factor de planta neto [%]": 30}, "NREL WIND Toolkit", False) == []


def test_pdf_report_full_and_partial(lib):
    from vientomx import pdf_report

    lay = layout.grid_layout(16.55, -94.95, 2, 3, 7, 3, 130, 0)
    m = met.synthetic_met(years=(2015, 2016))
    g, _, _ = terrain.make_grid(lay.lat, lay.lon, 4, 200)
    terrain.flat_terrain(g, 30, 0.05)
    t = lib["IEA Reference 3.4 MW D130"]
    cfg = aep.SimConfig(hub_height=110)
    r = aep.run_aep(lay, climate.extrapolate_to_height(m, 110), t, cfg, g, 1.2)
    pdf = pdf_report.build_pdf(pdf_report.Study(layout=lay, met=m, turbine=t, hub_height=110, grid=g, cfg=cfg,
                                                results=r, project="Prueba"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 50_000
    # solo con layout: las etapas faltantes quedan como pendientes, sin error
    assert pdf_report.build_pdf(pdf_report.Study(layout=lay)).startswith(b"%PDF")
