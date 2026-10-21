"""Minería: mantenimiento predictivo de equipos (datos sintéticos de sensores)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agents.guardrails import validator
from app.agents.tools import tool
from sectors import load_json

DATA = Path(__file__).parent / "data"
EQUIPOS = {e["id"]: e for e in load_json(DATA / "equipos.json")}
ORDENES: list[dict] = []


@tool(description="Consulta un equipo y sus lecturas de sensores (vibración, temperatura) con sus umbrales.",
      params={"equipo_id": "ID del equipo, ej. CAM-07"})
def consultar_equipo(equipo_id: str) -> dict:
    return EQUIPOS.get(equipo_id.strip().upper()) or {"error": f"No existe el equipo {equipo_id}"}


@tool(description="Programa una orden de mantenimiento para un equipo.",
      params={"equipo_id": "ID del equipo", "prioridad": "baja | media | alta | urgente", "descripcion": "Trabajo a realizar"})
def programar_mantenimiento(equipo_id: str, prioridad: str, descripcion: str) -> dict:
    orden = {"orden_id": f"OT-{len(ORDENES) + 1:04d}", "equipo_id": equipo_id.strip().upper(),
             "prioridad": prioridad, "descripcion": descripcion, "creada": datetime.now().isoformat(timespec="seconds")}
    ORDENES.append(orden)
    return orden


@tool(description="Detiene un equipo en operación por seguridad. Acción sensible: requiere aprobación humana.",
      params={"equipo_id": "ID del equipo", "motivo": "Motivo de la detención"}, sensitive=True)
def detener_equipo(equipo_id: str, motivo: str) -> dict:
    eq = EQUIPOS[equipo_id.strip().upper()]
    eq["estado"] = "detenido"
    return {"equipo_id": eq["id"], "estado": "detenido", "motivo": motivo,
            "fecha": datetime.now().isoformat(timespec="seconds")}


@validator("detener_equipo")
def equipo_operando(args: dict) -> str | None:
    eq = EQUIPOS.get(str(args.get("equipo_id", "")).strip().upper())
    if eq is None:
        return "El equipo no existe"
    if eq["estado"] != "operando":
        return f"El equipo {eq['id']} ya está {eq['estado']}"
    return None


@validator("programar_mantenimiento")
def prioridad_valida(args: dict) -> str | None:
    if str(args.get("prioridad", "")).lower() not in ("baja", "media", "alta", "urgente"):
        return "Prioridad inválida: use baja, media, alta o urgente"
    return None
