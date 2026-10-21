"""Endpoints de consultas, trazas y dashboard (frente B). Contrato en docs/CONTRATOS.md."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.normas import db

router = APIRouter(tags=["consultas"])


class ConsultaRequest(BaseModel):
    pregunta: str
    canal: str = "web"
    usuario: str = "anonimo"


@router.post("/consultas")
async def consultar(req: ConsultaRequest) -> dict:
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta está vacía")
    from app.normas.consultas import responder  # import diferido: registra las tools al usarse
    return await run_in_threadpool(responder, req.pregunta.strip(), req.canal, req.usuario)


_COLS = "run_id, agente, canal, usuario, modelo, entrada, salida, latencia_ms, estado, created_at"


@router.get("/trazas")
def listar_trazas(agente: str | None = None, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 500))
    if agente:
        return db.query(f"SELECT {_COLS} FROM trazas WHERE agente = ? ORDER BY created_at DESC LIMIT ?",
                        (agente, limit))
    return db.query(f"SELECT {_COLS} FROM trazas ORDER BY created_at DESC LIMIT ?", (limit,))


@router.get("/trazas/{run_id}")
def detalle_traza(run_id: str) -> dict:
    filas = db.query("SELECT * FROM trazas WHERE run_id = ?", (run_id,))
    if not filas:
        raise HTTPException(404, f"Traza {run_id} no encontrada")
    fila = filas[0]
    try:
        fila["pasos"] = json.loads(fila.pop("pasos_json") or "[]")
    except json.JSONDecodeError:
        fila["pasos"] = []
    consulta = db.query("SELECT pregunta, confianza, top_score, citas_json, advertencias_json FROM consultas "
                        "WHERE run_id = ?", (run_id,))
    if consulta:
        c = consulta[0]
        fila["consulta"] = {"pregunta": c["pregunta"], "confianza": c["confianza"], "top_score": c["top_score"],
                            "citas": _loads(c["citas_json"]), "advertencias": _loads(c["advertencias_json"])}
    return fila


def _loads(txt: str | None) -> list:
    try:
        v = json.loads(txt or "[]")
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


@router.get("/dashboard")
def dashboard() -> dict:
    consultas = db.query("SELECT pregunta, tema, cubierta, citas_json, latencia_ms, created_at FROM consultas")

    normas: Counter = Counter()
    nombres: dict[str, str] = {}
    sin_cob: dict[str, list[str]] = defaultdict(list)
    for c in consultas:
        for doc_id in {x.get("doc_id") for x in _loads(c["citas_json"]) if isinstance(x, dict) and x.get("doc_id")}:
            normas[doc_id] += 1
        for x in _loads(c["citas_json"]):
            if isinstance(x, dict) and x.get("doc_id"):
                nombres.setdefault(x["doc_id"], x.get("nombre_norma") or x["doc_id"])
        if not c["cubierta"]:
            sin_cob[c["tema"] or "otros"].append(c["pregunta"])

    top_normas = [{"doc_id": d, "nombre_norma": nombres.get(d, d), "n": n} for d, n in normas.most_common(10)]
    sin_cobertura = sorted(({"tema": t, "n": len(p), "ejemplos": p[-3:][::-1]} for t, p in sin_cob.items()),
                           key=lambda x: x["n"], reverse=True)

    disc = db.query("SELECT tipo, COUNT(*) n FROM discrepancias WHERE estado_revision != 'descartada' GROUP BY tipo")
    recientes = db.query(
        "SELECT id, tipo, severidad, doc_a, cita_a, doc_b, cita_b, explicacion, estado_revision, created_at "
        "FROM discrepancias WHERE estado_revision != 'descartada' ORDER BY created_at DESC, id DESC LIMIT 5")

    hoy = date.today().isoformat()
    lat = [c["latencia_ms"] for c in consultas if c["latencia_ms"] is not None]
    total = len(consultas)
    uso = {
        "consultas_total": total,
        "consultas_hoy": sum(1 for c in consultas if (c["created_at"] or "").startswith(hoy)),
        "pct_cubiertas": round(100 * sum(1 for c in consultas if c["cubierta"]) / total, 1) if total else 0,
        "latencia_p50_ms": int(statistics.median(lat)) if lat else 0,
        "documentos": db.query("SELECT COUNT(*) n FROM documentos")[0]["n"],
        "chunks": db.query("SELECT COUNT(*) n FROM chunks")[0]["n"],
    }
    return {"top_normas": top_normas, "sin_cobertura": sin_cobertura,
            "discrepancias": {"total": sum(d["n"] for d in disc), "por_tipo": {d["tipo"]: d["n"] for d in disc},
                              "recientes": recientes},
            "uso": uso}
