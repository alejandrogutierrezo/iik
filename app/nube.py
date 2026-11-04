"""Ajustes para publicar iik en un servidor compartido (p. ej. Streamlit Community Cloud).

En un servidor así todos los usuarios comparten un solo proceso de Python y poca memoria. Este módulo:

* copia los secretos de primer nivel (``st.secrets``) a variables de entorno, para que ``cdsapi`` lea
  ``CDSAPI_URL``/``CDSAPI_KEY`` y ``vientomx`` lea sus variables ``VIENTOMX_*``;
* detecta el «modo servidor compartido» (``IIK_SERVIDOR_COMPARTIDO=1``, o automáticamente en Streamlit
  Community Cloud);
* limita cuántos cálculos pesados (terreno, AEP) corren a la vez y el tamaño de la malla de terreno.

Variables (en el panel de *secrets* o en el entorno):

``IIK_SERVIDOR_COMPARTIDO``  1 / 0. Si no se define, se activa sola en Streamlit Community Cloud.
``IIK_TAREAS_SIMULTANEAS``   cálculos pesados a la vez (por defecto 2 en modo compartido; 0 = sin límite).
``IIK_MAX_CELDAS``           celdas máximas de la malla de terreno (por defecto 400 000 en modo compartido).
"""
from __future__ import annotations

import contextlib
import gc
import os
import threading
import time
from pathlib import Path

import streamlit as st


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, default)), 0)
    except ValueError:
        return default


def cargar_secretos() -> None:
    """Pasa los secretos de primer nivel a variables de entorno (sin pisar las que ya existen).

    Llamar antes de importar ``vientomx``, que lee algunas variables al importarse."""
    try:
        items = dict(st.secrets)
    except Exception:  # no hay secrets.toml: nada que cargar
        return
    for k, v in items.items():
        if isinstance(v, (str, int, float, bool)):
            os.environ.setdefault(str(k), str(v))


def compartido() -> bool:
    """True si la app corre en un servidor que comparten varios usuarios."""
    v = os.environ.get("IIK_SERVIDOR_COMPARTIDO")
    if v is not None and v.strip():
        return _truthy(v)
    # Streamlit Community Cloud clona el repositorio en /mount/src/<repo>
    return Path(__file__).resolve().as_posix().startswith("/mount/src/")


def max_celdas() -> int:
    """Celdas máximas de la malla de terreno (0 = sin límite)."""
    return _int_env("IIK_MAX_CELDAS", 400_000 if compartido() else 0)


def max_tareas() -> int:
    """Cálculos pesados que pueden correr a la vez en el proceso (0 = sin límite)."""
    return _int_env("IIK_TAREAS_SIMULTANEAS", 2 if compartido() else 0)


@st.cache_resource
def _semaforo(n: int) -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(n)  # uno por proceso, compartido por todas las sesiones


@contextlib.contextmanager
def turno(que: str = "El cálculo"):
    """Espera un lugar libre antes de un cálculo pesado y libera memoria al terminar.

    Sin límite configurado no hace nada. Mientras espera, actualiza un aviso cada segundo; así Streamlit
    puede interrumpir la espera si el usuario cambia algo o cierra la página."""
    n = max_tareas()
    if n <= 0:
        yield
        return
    sem = _semaforo(n)
    if not sem.acquire(blocking=False):
        aviso, t0 = st.empty(), time.monotonic()
        while not sem.acquire(timeout=1.0):
            aviso.info(f"Hay otros estudios calculándose en el servidor. {que} empezará en cuanto haya lugar "
                       f"(esperando {time.monotonic() - t0:.0f} s)…")
        aviso.empty()
    try:
        yield
    finally:
        sem.release()
        gc.collect()
