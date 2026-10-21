"""Endpoint de KPIs: GET /api/kpis. Cálculo en app/normas/kpis.py.

Contrato en docs/CONTRATOS.md (sección KPIs). valor null = sin datos.
"""
from fastapi import APIRouter

from app.normas import kpis as _kpis

router = APIRouter(tags=["kpis"])


@router.get("/kpis")
def kpis(desde: str | None = None, hasta: str | None = None, umbral: float | None = None,
         tiempo_manual_min: float = 15, granularidad: str = "hora") -> dict:
    return _kpis.calcular(desde, hasta, umbral, tiempo_manual_min, granularidad)
