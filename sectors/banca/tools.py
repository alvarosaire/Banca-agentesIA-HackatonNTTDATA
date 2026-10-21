"""Banca: evaluación de solicitudes de crédito (datos sintéticos)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agents.guardrails import validator
from app.agents.tools import tool
from sectors import load_json

DATA = Path(__file__).parent / "data"
CLIENTES = {c["id"]: c for c in load_json(DATA / "clientes.json")}
SOLICITUDES = {s["id"]: s for s in load_json(DATA / "solicitudes.json")}
SCORE_MINIMO = 600


@tool(description="Consulta una solicitud de crédito (cliente, producto, monto, plazo).",
      params={"solicitud_id": "ID de la solicitud, ej. SOL-001"})
def consultar_solicitud(solicitud_id: str) -> dict:
    return SOLICITUDES.get(solicitud_id.strip().upper()) or {"error": f"No existe la solicitud {solicitud_id}"}


@tool(description="Consulta el perfil crediticio de un cliente (score, ingresos, deudas, mora).",
      params={"cliente_id": "ID del cliente, ej. CLI-100"})
def consultar_cliente(cliente_id: str) -> dict:
    return CLIENTES.get(cliente_id.strip().upper()) or {"error": f"No existe el cliente {cliente_id}"}


@tool(description="Aprueba y desembolsa un crédito. Acción sensible: requiere aprobación humana.",
      params={"solicitud_id": "ID de la solicitud", "monto": "Monto a aprobar", "tasa_anual": "Tasa anual en %"},
      sensitive=True)
def aprobar_credito(solicitud_id: str, monto: float, tasa_anual: float) -> dict:
    return {"solicitud_id": solicitud_id.strip().upper(), "monto": monto, "tasa_anual": tasa_anual,
            "estado": "aprobado", "fecha": datetime.now().isoformat(timespec="seconds")}


@validator("aprobar_credito")
def politica_credito(args: dict) -> str | None:
    sol = SOLICITUDES.get(str(args.get("solicitud_id", "")).strip().upper())
    if sol is None:
        return "La solicitud no existe"
    cli = CLIENTES[sol["cliente_id"]]
    if cli["score"] < SCORE_MINIMO or cli["mora"]:
        return f"Política: score {cli['score']} (mínimo {SCORE_MINIMO}) o cliente en mora"
    if float(args.get("monto", 0)) > sol["monto"]:
        return "No se puede aprobar un monto mayor al solicitado"
    return None
