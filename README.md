# VientoMX 🌬️

**Evaluación de recurso eólico (Wind Resource Assessment) y producción anual de
parques eólicos, pensada para México y para estudiantes universitarios.**

Interfaz web local en español (Streamlit) sobre un motor en Python. En seis
pasos se pasa de una lista de coordenadas a la AEP P50/P90 del parque:

1. **Sitio y layout**: selección de turbinas con clic sobre un mapa de
   OpenStreetMap (colocar, mover, borrar) e importación de los aerogeneradores
   que ya están mapeados en OSM para parques construidos; carga de coordenadas desde CSV, Excel o Google Earth
   (KML/KMZ), en lat/lon o UTM; o un layout rectangular generado en una de las
   regiones eólicas de México (Istmo, Tamaulipas, Nuevo León, La Rumorosa,
   altiplano, Yucatán…).
2. **Viento**: descarga de 10–40+ años de datos horarios de **ERA5** (vía
   Open-Meteo, sin registro), **NASA POWER/MERRA-2** (sin registro) o **ERA5 del
   Copernicus CDS** (API key gratuita); o carga de una **torre de medición/LiDAR**
   con ajuste de largo plazo **MCP**. Rosa de vientos, Weibull, ciclos diario y
   anual, cizalladura por sector y hora.
3. **Terreno y rugosidad**: descarga automática de **Copernicus DEM 30 m** y
   **ESA WorldCover 10 m** (o GeoTIFF propio, p. ej. INEGI CEM 3.0); mapas de
   elevación, rugosidad, aceleración orográfica y RIX por turbina.
4. **Aerogenerador**: librería de ~136 curvas de potencia (NREL turbine-models +
   OEDB/windpowerlib: Vestas, Siemens, Nordex, Enercon, GE…) o curva propia.
5. **Simulación**: orografía + rugosidad + estelas (Jensen o Gaussiano de
   Bastankhah) por dirección, corrección por densidad, pérdidas editables.
6. **Resultados**: AEP bruta/neta por turbina, P50/P75/P90/P99, mapa de estelas,
   perfil 12×24, producción por año, rosa de energía, exportación a Excel,
   serie horaria CSV y **reporte PDF** del estudio completo (configuración,
   supuestos y advertencias de cada etapa, y resultados con tablas y gráficas).

La fidelidad objetivo está **entre SAM y WAsP**. El detalle está en
[`docs/metodo.md`](docs/metodo.md) (también visible en la pestaña *Método*).

## Instalación

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/alejandrogutierrezo/iik.git
cd iik
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/Mac: source .venv/bin/activate
pip install -e .
# opcional, para ERA5 directo del CDS:
pip install -e ".[era5]"
```

## Uso

```bash
vientomx app          # abre la interfaz en el navegador (http://localhost:8501)
vientomx demo         # caso de prueba sin internet, imprime resultados
```

En la interfaz, el botón **"Cargar caso demo"** llena todos los pasos con un
ejemplo en La Ventosa, Oaxaca (viento sintético), útil para explorar sin
descargar nada.

Como librería: ver [`examples/ejemplo_la_ventosa.py`](examples/ejemplo_la_ventosa.py).

### Formato del archivo de coordenadas

```csv
id,lat,lon,hub_height
WTG01,16.5500,-94.9500,110
WTG02,16.5530,-94.9500,110
```

También se reconocen `nombre/latitud/longitud` y `x/y`, `este/norte` (UTM, se
indica la zona: 11 en Baja California a 16 en Yucatán). Separador coma, punto y
coma o tabulador. KML/KMZ: cada Placemark de tipo punto es una turbina.

### Seleccionar turbinas en el mapa (OpenStreetMap)

En el paso 1, modo **Seleccionar en el mapa**:

* **Buscar turbinas de OSM aquí** consulta la Overpass API y muestra en naranja
  los nodos `power=generator` + `generator:source=wind` del área visible (el
  icono de aerogenerador de openstreetmap.org). Se toman con un clic, o todas con
  **Usar todas las de OSM**. Se conservan `ref`/`name` como id, y la altura de
  buje si está etiquetada (`height:hub`); la columna `osm_id` identifica su origen.
* Con **Agregar / Mover / Borrar turbina** se edita el layout haciendo clic; las
  coordenadas también se pueden corregir en la tabla.
* El fondo **Satélite (Esri)** ayuda a verificar que las posiciones de OSM
  coinciden con las torres reales; OSM es colaborativo y puede tener errores u
  omisiones.
* Si la red bloquea los servidores públicos de Overpass, defina
  `VIENTOMX_OVERPASS_URL` con otro servidor.

Datos de turbinas © colaboradores de OpenStreetMap, licencia ODbL.

## Fuentes de datos

| Insumo | Fuente | Notas para México |
|---|---|---|
| Viento horario | ERA5 vía Open-Meteo | 0.25° (~28 km), 1940–presente, 10 y 100 m. Bueno en terreno plano. |
| Viento horario | **NREL WIND Toolkit México** | Modelo WRF de mesoescala, ~2 km, 2007–2014, 10–200 m, AWS Open Data sin registro. **Recomendada en sierras, cañones y pasos de montaña.** Se descargan solo los bloques del punto (~40 MB/año). |
| Viento horario | NASA POWER (MERRA-2) | ~50 km, 2001–presente, 10 y 50 m. Segunda opinión. |
| Viento horario | ERA5 Copernicus CDS | El mismo dato que ERA5 vía Open-Meteo, descarga oficial (lenta). |
| Viento medio 250 m | Global Wind Atlas | Calibra la media de la serie a 10/50/100/150/200 m: botón automático (GeoTIFF nacional, lectura parcial o descarga única a caché) o GeoTIFF subido. |
| Elevación | Copernicus DEM GLO-30 | Automático. Alternativa: INEGI CEM 3.0 (15 m) como GeoTIFF. |
| Cobertura de suelo | ESA WorldCover 2021 | Automático, tabla z0 editable. Alternativa: INEGI USV Serie VII. |

Los datos descargados se guardan en caché en `~/.vientomx/cache`.

**Terreno complejo.** Los reanálisis globales (ERA5, MERRA-2) promedian celdas de
25–50 km y no ven el encauzamiento del viento en cañones y pasos de montaña. En el
parque eólico Santa Catarina (N.L.), por ejemplo, ERA5 da 3.1 m/s a 85 m, mientras
que el WIND Toolkit da 5.4–7.9 m/s (2010, 100 m) en los puntos de malla a menos de 3 km, según su
posición y elevación. En esos sitios use el WIND Toolkit, calibre con el Global Wind
Atlas o, mejor, con una medición; la app avisa cuando el RIX medio supera 5 % y la
fuente es un reanálisis sin calibrar.

## Librería de curvas de potencia: fuentes sugeridas

Incluidas automáticamente (se leen de los paquetes instalados, no se redistribuyen):

* **NREL / NatLabRockies `turbine-models`** (BSD-3): ~70 turbinas de referencia
  y algunas comerciales (GE 1.5, Vestas V47/V82, IEA 3.4/10/15 MW, NREL 5 MW,
  promedios de mercado ATB), muchas con curva Ct.
  <https://github.com/NatLabRockies/turbine-models>
* **OEDB vía `windpowerlib`** (Open Energy Platform): ~70 modelos comerciales
  con curva de potencia (Enercon, Vestas, Siemens, Nordex, GE, Senvion, Vensys,
  Eno). Ct estimado con teoría de disco actuador calibrada.
  <https://openenergyplatform.org/dataedit/view/supply/wind_turbine_library>

Para ampliar la librería (cargar como CSV propio):

* **Fichas técnicas de los fabricantes** (Vestas, Siemens Gamesa, Nordex, GE
  Vernova, Goldwind, Envision, Mingyang). Son la fuente válida para estudios
  formales; suelen pedirse bajo NDA.
* **thewindpower.net**: base de datos amplia de modelos y algunas curvas
  (uso no comercial, revisar términos).
* **wind-turbine-models.com**: curvas digitalizadas de muchos modelos.
* **Renewables.ninja** (Staffell & Pfenninger): ~100 curvas de potencia en su
  repositorio público de datos.
* **PyWake / FLORIS**: incluyen turbinas de referencia con curvas Ct.
* **IEA Wind Task 37**: turbinas de referencia 3.4/10/15/22 MW en formato
  `windIO`.

Formato del CSV propio: columnas `ws` (m/s), `power_kw` y opcional `ct`.

## Relación con otros proyectos

* **Continuum (One Energy)**: modelo de flujo distinto (basado en mediciones y
  exposición del terreno), escrito en C#/Windows. VientoMX toma la idea de
  trabajar por sector con DEM y rugosidad, pero con un modelo lineal FFT.
* **brightwind**: análisis de mediciones (MCP, cizalladura, limpieza). VientoMX
  implementa un MCP básico; para análisis de torre a fondo, brightwind es buen
  complemento.
* **OpenOA (NREL)**: análisis de parques en operación (SCADA), no evaluación
  previa. Puede usarse después para validar la AEP contra datos reales.
* **awesome-wind** (IEA Wind): catálogo de herramientas; PyWake, FLORIS y
  windpowerlib son alternativas o extensiones naturales del motor de estelas.

## Limitaciones conocidas y siguientes pasos

* Una sola turbina y altura de buje por parque (falta layout mixto).
* Modelo orográfico lineal: sobrestima la aceleración en terreno escarpado
  (RIX > 5 %); se reporta y se suma incertidumbre. Siguiente paso: corrección
  ΔRIX o acoplar un modelo CFD.
* No modela estabilidad atmosférica en las estelas ni el efecto de bloqueo.
* Sin optimización de layout (posible extensión con PyWake/TOPFARM).
* Sin guardado de proyectos (`.json` / `.zip`): pendiente.

## Pruebas

```bash
pip install -e ".[dev]"
pytest
```

Incluyen la verificación del modelo orográfico contra la solución analítica de
una colina de Agnesi, la del déficit de Jensen y un caso completo de AEP.

## Licencia

Código bajo GPL-3.0 (ver `LICENSE`). Los datos descargados mantienen sus licencias (Copernicus, ESA
WorldCover CC-BY 4.0, Open-Meteo CC-BY 4.0, NASA POWER dominio público, OEDB,
NREL BSD-3). Cite las fuentes en sus reportes.
