"""Tools de ejemplo (genéricas, con datos simulados). Reemplazar por las del sector."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agents.guardrails import validator
from app.agents.tools import tool
from sectors import load_json

DATA = Path(__file__).parent / "data"
ENTIDADES: dict[str, dict] = {e["id"]: e for e in load_json(DATA / "entidades.json")}
ORDENES: list[dict] = []


@tool(description="Consulta los datos de una entidad (cliente, activo, equipo...) por su ID.",
      params={"entidad_id": "ID de la entidad, ej. ENT-001"})
def consultar_entidad(entidad_id: str) -> dict:
    ent = ENTIDADES.get(entidad_id.strip().upper())
    return ent or {"error": f"No existe la entidad {entidad_id}"}


@tool(description="Crea una orden/acción por un monto para una entidad. Acción sensible: requiere aprobación.",
      params={"entidad_id": "ID de la entidad", "monto": "Monto de la orden", "descripcion": "Motivo de la orden"},
      sensitive=True)
def crear_orden(entidad_id: str, monto: float, descripcion: str) -> dict:
    orden = {
        "orden_id": f"ORD-{len(ORDENES) + 1:04d}",
        "entidad_id": entidad_id.strip().upper(),
        "monto": monto,
        "descripcion": descripcion,
        "creada": datetime.now().isoformat(timespec="seconds"),
        "estado": "creada",
    }
    ORDENES.append(orden)
    return orden


@validator("crear_orden")
def entidad_debe_estar_activa(args: dict) -> str | None:
    ent = ENTIDADES.get(str(args.get("entidad_id", "")).strip().upper())
    if ent is None:
        return "La entidad no existe"
    if ent["estado"] != "activo":
        return f"La entidad {ent['id']} está {ent['estado']}; no se pueden crear órdenes"
    return None
