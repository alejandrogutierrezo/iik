"""Exportación de resultados (Excel y CSV)."""
from __future__ import annotations

import io

import pandas as pd

from .aep import Results


def results_to_excel(res: Results, inputs: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(list(res.summary.items()), columns=["Concepto", "Valor"]).to_excel(xw, sheet_name="Resumen", index=False)
        if inputs:
            pd.DataFrame(list(inputs.items()), columns=["Parámetro", "Valor"]).to_excel(xw, sheet_name="Entradas", index=False)
        res.per_turbine.to_excel(xw, sheet_name="Por turbina", index=False)
        res.losses.to_excel(xw, sheet_name="Pérdidas", index=False)
        res.uncertainty.to_excel(xw, sheet_name="Incertidumbre", index=False)
        res.sector.to_excel(xw, sheet_name="Por sector", index=False)
        res.annual.to_excel(xw, sheet_name="Por año")
        res.monthly_diurnal.round(2).to_excel(xw, sheet_name="12x24 (FP %)")
    return buf.getvalue()


def hourly_to_csv(res: Results) -> bytes:
    s = res.hourly_farm_kw.tz_convert("America/Mexico_City")
    df = pd.DataFrame({"hora_local": s.index.tz_localize(None), "potencia_neta_MW": (s / 1e3).round(3).to_numpy()})
    return df.to_csv(index=False).encode()
