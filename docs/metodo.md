# Método de cálculo de VientoMX

VientoMX busca una fidelidad **intermedia entre SAM y WAsP**: más que SAM (que usa
un solo recurso para todo el parque y estelas simplificadas) porque modela cada
aerogenerador con su terreno, rugosidad y estelas por dirección; menos que WAsP
porque usa modelos lineales simplificados y no resuelve la estabilidad atmosférica
ni la capa interna del flujo sobre colinas.

## 1. Clima de viento de referencia

* **Reanálisis** (ERA5 vía Open-Meteo, NASA POWER/MERRA-2 o ERA5 del CDS): serie
  horaria de 10 a 40+ años en el punto más cercano al parque. Representa el viento
  promedio de una celda de ~30 km, por eso sirve como "clima regional".
* **Medición en sitio** (torre o LiDAR): opcionalmente ajustada al largo plazo con
  **MCP por razón de varianzas** por sector contra el reanálisis.
* **Calibración opcional** a una velocidad media objetivo (por ejemplo el
  Global Wind Atlas, que tiene 250 m de resolución), útil porque el reanálisis
  suele subestimar el viento en el Istmo de Tehuantepec y en sierras.

## 2. Extrapolación a la altura de buje

Ley potencial `U(z) = U(z_ref)·(z/z_ref)^α` con α calculado de las dos alturas
del reanálisis (10 y 100 m, o 10 y 50 m) **por sector de dirección y hora local
del día**, lo que captura el efecto de la estabilidad atmosférica (vientos
nocturnos más cizallados).

## 3. Densidad del aire

Calculada con temperatura y presión del reanálisis y corregida por la elevación
del sitio y la altura de buje. Es importante en México: en el altiplano
(Zacatecas, SLP, Puebla) la densidad baja a ~0.95–1.05 kg/m³, lo que reduce la
producción 10–20 % frente a la curva estándar. La curva de potencia se ajusta con
el método de IEC 61400-12-1 (`u·(ρ/1.225)^(1/3)`).

## 4. Modelo de flujo: terreno

Teoría lineal de flujo potencial (capa externa de Jackson & Hunt, 1975), resuelta
con transformada de Fourier sobre la malla del modelo de elevación:

`û(k) = Ĥ(k) · k∥² / |k| · exp(−|k| z)`

donde `k∥` es la componente del número de onda en la dirección del viento y `z` la
altura sobre el terreno. Se calcula para 12 sectores. Las ondas mayores a 30 km se
filtran (ese relieve ya está en el reanálisis). El resultado se verificó contra la
solución analítica de una colina 2-D de Agnesi (error < 1 %).

**Limitación**: en terreno escarpado (RIX > 5 %, pendientes > 30 %) los modelos
lineales sobrestiman la aceleración; la herramienta reporta el RIX por turbina y
agrega incertidumbre. Para esos sitios se recomienda CFD o WAsP con corrección RIX.

## 5. Modelo de flujo: rugosidad

* Mapa de rugosidad `z0` a partir de ESA WorldCover 2021 (10 m) con una tabla de
  equivalencias editable.
* `z0` efectivo aguas arriba de cada turbina por sector: promedio logarítmico con
  peso `exp(−r/2.5 km)` hasta 10 km.
* Corrección logarítmica suponiendo el mismo viento a una altura de mezcla
  (300 m por defecto): `U ∝ ln(z/z0)/ln(h_b/z0)`. Con reanálisis la referencia es
  la rugosidad media del dominio; con torre es la rugosidad aguas arriba de la torre.

## 6. Estelas

* **Jensen/Park** (k = 0.075 en tierra) con traslape parcial de estela y rotor y
  superposición por suma de cuadrados.
* **Gaussiano de Bastankhah & Porté-Agel (2014)**, con `k* = 0.38·TI + 0.004`.

Se calculan para 36 direcciones (cada una promediada en 3 subdirecciones) y
velocidades de 0 a 35 m/s cada 0.5 m/s, resolviendo de aguas arriba hacia aguas
abajo con el Ct de cada turbina a su velocidad local.

## 7. Producción y pérdidas

La serie horaria completa se pasa por las tablas de potencia (interpolando en
velocidad), lo que da:

* AEP bruta (sin estelas) y con estelas por turbina.
* Serie horaria de producción del parque, perfiles 12×24 y producción por año.
* AEP neta P50 = AEP con estelas × Π(1 − pérdidas) (disponibilidad, eléctricas,
  desempeño, ambientales, curtailment; editables).

## 8. Incertidumbre y P90

Las incertidumbres de velocidad se convierten a energía con la **sensibilidad**
calculada (`ΔE/E ÷ ΔU/U`, típicamente 1.2–2.0). Se combinan como suma cuadrática
con la representatividad de largo plazo (`IAV/√años`) y la variabilidad futura
(1 o 10 años). `P90 = P50·(1 − 1.282·σ)`.

## Fuentes de datos

| Insumo | Fuente | Resolución | Acceso |
|---|---|---|---|
| Viento horario | ERA5 (ECMWF/Copernicus) vía Open-Meteo | 0.25°, 1940–hoy | libre, sin registro |
| Viento horario | NASA POWER (MERRA-2) | 0.5°×0.625°, 2001–hoy | libre, sin registro |
| Viento horario | ERA5 en Copernicus CDS | 0.25° | registro gratuito |
| Viento medio | Global Wind Atlas 3 (DTU) | 250 m | libre (calibración manual) |
| Elevación | Copernicus DEM GLO-30 | 30 m | libre (AWS Open Data) |
| Elevación | INEGI CEM 3.0 | 15 m | descarga manual, carga como GeoTIFF |
| Uso de suelo | ESA WorldCover 2021 | 10 m | libre (AWS Open Data) |
| Uso de suelo | INEGI Uso de Suelo y Vegetación Serie VII | 1:250 000 | convertir a GeoTIFF z0 |
| Curvas de potencia | NREL turbine-models | — | BSD-3 |
| Curvas de potencia | OEDB / windpowerlib | ~70 modelos comerciales | Open Energy Platform |

## Referencias

* Jackson, P.S. & Hunt, J.C.R. (1975). Turbulent wind flow over a low hill. *QJRMS* 101.
* Troen, I. & Petersen, E.L. (1989). *European Wind Atlas*. Risø.
* Jensen, N.O. (1983). A note on wind generator interaction. Risø-M-2411.
* Katic, I., Højstrup, J. & Jensen, N.O. (1986). A simple model for cluster efficiency. EWEC.
* Bastankhah, M. & Porté-Agel, F. (2014). A new analytical model for wind-turbine wakes. *Renewable Energy* 70.
* Niayifar, A. & Porté-Agel, F. (2016). Analytical modeling of wind farms. *Energies* 9.
* Bowen, A.J. & Mortensen, N.G. (2004). WAsP prediction errors due to site orography. Risø-R-995 (RIX).
* IEC 61400-12-1:2017 (corrección de densidad de la curva de potencia).
* Hersbach, H. et al. (2020). The ERA5 global reanalysis. *QJRMS* 146.
