"""Manufactura: control de calidad de lotes y replanificación de producción (datos sintéticos)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agents.guardrails import validator
from app.agents.tools import tool
from sectors import load_json

DATA = Path(__file__).parent / "data"
LOTES = {lote["id"]: lote for lote in load_json(DATA / "lotes.json")}
LINEAS = {"L1", "L2", "L3"}
PLAN: list[dict] = []


@tool(description="Consulta un lote de producción: producto, línea, unidades y resultados de inspección de calidad.",
      params={"lote_id": "ID del lote, ej. L-2041"})
def consultar_lote(lote_id: str) -> dict:
    return LOTES.get(lote_id.strip().upper()) or {"error": f"No existe el lote {lote_id}"}


@tool(description="Retiene un lote para que no se despache. Acción sensible: requiere aprobación humana.",
      params={"lote_id": "ID del lote", "motivo": "Motivo de la retención"}, sensitive=True)
def retener_lote(lote_id: str, motivo: str) -> dict:
    lote = LOTES[lote_id.strip().upper()]
    lote["estado"] = "retenido"
    return {"lote_id": lote["id"], "estado": "retenido", "unidades": lote["unidades"], "motivo": motivo}


@tool(description="Agrega una orden de producción al plan de una línea para reponer unidades.",
      params={"linea": "Línea (L1, L2 o L3)", "producto": "Producto a fabricar", "cantidad": "Unidades a producir"})
def replanificar_linea(linea: str, producto: str, cantidad: int) -> dict:
    orden = {"orden_id": f"OP-{len(PLAN) + 1:04d}", "linea": linea.strip().upper(), "producto": producto,
             "cantidad": cantidad, "creada": datetime.now().isoformat(timespec="seconds")}
    PLAN.append(orden)
    return orden


@validator("retener_lote")
def lote_retenible(args: dict) -> str | None:
    lote = LOTES.get(str(args.get("lote_id", "")).strip().upper())
    if lote is None:
        return "El lote no existe"
    if lote["estado"] == "retenido":
        return f"El lote {lote['id']} ya está retenido"
    return None


@validator("replanificar_linea")
def linea_valida(args: dict) -> str | None:
    if str(args.get("linea", "")).strip().upper() not in LINEAS:
        return f"Línea inválida. Opciones: {sorted(LINEAS)}"
    return None
