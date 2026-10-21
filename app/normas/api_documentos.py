"""Endpoints del Agente 1 — ingesta y validación de normas. Contrato en docs/CONTRATOS.md (frente A)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Form, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.normas import db
from app.normas.areas import AREAS, de_json
from app.normas.ingesta import (DECISIONES, ArchivoRechazado, decidir, editar_areas, es_archivo_prohibido, procesar,
                                recibir)

log = logging.getLogger("api_documentos")
router = APIRouter(tags=["documentos"])

UPLOADS = db.ROOT / "data" / "uploads"
EXTENSIONES = (".pdf", ".docx", ".txt", ".md")
COLS_DOC = ("doc_id, codigo, nombre_norma, entidad, tipo_fuente, tema, fecha_emision, fecha_vigencia, estado, "
            "estado_inferido, version, reemplaza_a, reemplazada_por, publicado, requiere_confirmacion, "
            "estado_ingesta, etapa, mensaje_ingesta, n_chunks, run_id, created_at, areas_aplica, areas_fuente, "
            "areas_confianza, COALESCE(areas_requiere_confirmacion, 1) AS areas_requiere_confirmacion")
COLS_DISC = ("x.id, x.tema, x.severidad, x.tipo, x.doc_a, x.chunk_a, x.art_a, x.version_a, x.cita_a, x.doc_b, "
             "x.chunk_b, x.art_b, x.version_b, x.cita_b, x.prevalece, x.motivo, x.explicacion, "
             "COALESCE(x.estado, 'abierta') AS estado, x.estado_revision, x.comentario, x.run_id, x.created_at, "
             "da.nombre_norma AS nombre_a, db.nombre_norma AS nombre_b")
FROM_DISC = ("FROM discrepancias x LEFT JOIN documentos da ON da.doc_id = x.doc_a "
             "LEFT JOIN documentos db ON db.doc_id = x.doc_b")
ETAPAS = ("recibido", "extrayendo_metadatos", "indexando", "comparando", "informe", "listo", "requiere_revision",
          "error")


def _areas(d: dict) -> dict:
    if "areas_aplica" in d:
        d["areas_aplica"] = de_json(d["areas_aplica"])
    return d


def _doc(doc_id: str, cols: str = COLS_DOC) -> dict:
    rows = db.query(f"SELECT {cols} FROM documentos WHERE doc_id = ?", (doc_id,))
    if not rows:
        raise HTTPException(404, f"Documento {doc_id} no encontrado")
    return _areas(rows[0])


@router.post("/documentos", status_code=202)
async def subir_documento(archivo: UploadFile = File(...), areas: str = Form("")) -> JSONResponse:
    """`areas` (opcional, separadas por coma): áreas elegidas por el usuario al cargar; prevalecen sobre la ficha y el LLM."""
    original = Path(archivo.filename or "documento.txt").name
    areas_usuario: list[str] = []
    if areas.strip():
        from app.normas import areas as areas_mod
        areas_usuario, descartadas = areas_mod.normalizar_con_descartes(areas)
        if descartadas:
            raise HTTPException(400, f"Áreas no válidas: {descartadas}. Válidas: {areas_mod.AREAS}")
    if es_archivo_prohibido(original):
        raise HTTPException(400, f"'{original}' es material interno (00_GUIA / NO_SUBIR) y no se puede indexar")
    if Path(original).suffix.lower() not in EXTENSIONES:
        raise HTTPException(400, "Formato no soportado: usa PDF, DOCX, TXT o MD")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    nombre = re.sub(r"[^\w.\-]", "_", original)
    destino = UPLOADS / nombre
    if destino.exists():
        destino = UPLOADS / f"{int(time.time() * 1000)}_{nombre}"
    destino.write_bytes(await archivo.read())
    try:
        ctx = await asyncio.to_thread(recibir, destino, None, "web", original)
    except ArchivoRechazado as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        log.exception("Recepción de %s falló", original)
        raise HTTPException(500, f"No se pudo leer el archivo: {e}") from e
    if ctx.get("duplicado"):
        return JSONResponse({"doc_id": ctx["doc_id"], "codigo": ctx.get("codigo"),
                             "estado_ingesta": ctx["estado_ingesta"], "duplicado": True,
                             "mensaje": ctx.get("mensaje")}, status_code=202)
    if areas_usuario:
        ctx["areas_usuario"] = areas_usuario
    threading.Thread(target=procesar, args=(ctx,), kwargs={"canal": "web"}, name=f"ingesta-{ctx['doc_id']}",
                     daemon=True).start()
    return JSONResponse({"doc_id": ctx["doc_id"], "codigo": ctx.get("codigo"), "estado_ingesta": "procesando",
                         "duplicado": False, "mensaje": "Documento recibido: procesando en segundo plano"},
                        status_code=202)


@router.get("/documentos")
def listar_documentos() -> list[dict]:
    return [_areas(d) for d in db.query(f"SELECT {COLS_DOC} FROM documentos ORDER BY created_at DESC, rowid DESC")]


@router.get("/areas")
def listar_areas() -> list[str]:
    return list(AREAS)


class EdicionAreas(BaseModel):
    areas: list[str]
    usuario: str | None = None


@router.put("/documentos/{doc_id}/areas")
def editar_areas_documento(doc_id: str, body: EdicionAreas) -> dict:
    _doc(doc_id, "doc_id")
    try:
        return editar_areas(doc_id, body.areas, body.usuario or "web")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, f"Documento {doc_id} no encontrado") from e


@router.get("/documentos/{doc_id}/progreso")
def progreso(doc_id: str) -> dict:
    d = _doc(doc_id)
    etapa = d["etapa"] or "recibido"
    if d["estado_ingesta"] == "requiere_revision":
        etapa = "requiere_revision"
    elif d["estado_ingesta"] == "error":
        etapa = "error"
    n_altas = db.query("SELECT COUNT(*) n FROM discrepancias WHERE doc_a = ? AND severidad = 'alta' "
                       "AND COALESCE(estado, 'abierta') = 'abierta'", (doc_id,))[0]["n"]
    return {"doc_id": doc_id, "etapa": etapa if etapa in ETAPAS else "recibido", "mensaje": d["mensaje_ingesta"],
            "n_discrepancias_altas": n_altas, "estado": d["estado"], "publicado": d["publicado"],
            "estado_ingesta": d["estado_ingesta"]}


def _json(v: str | None) -> dict | list | None:
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


@router.get("/documentos/{doc_id}")
def detalle_documento(doc_id: str) -> dict:
    doc = _doc(doc_id, COLS_DOC + ", archivo, sha256, metadatos_json, confianza_json")
    doc["metadatos_json"] = _json(doc["metadatos_json"])
    doc["confianza_json"] = _json(doc["confianza_json"])
    doc["chunks"] = db.query("SELECT chunk_id, articulo, seccion, pagina, texto FROM chunks WHERE doc_id = ? "
                             "ORDER BY rowid", (doc_id,))
    doc["afirmaciones"] = db.query("SELECT id, chunk_id, articulo, tema, valor, unidad, condicion, sentido, "
                                   "cita_literal FROM afirmaciones WHERE doc_id = ? ORDER BY id", (doc_id,))
    doc["discrepancias"] = db.query(f"SELECT {COLS_DISC} {FROM_DISC} WHERE x.doc_a = ? OR x.doc_b = ? "
                                    "ORDER BY x.id DESC", (doc_id, doc_id))
    return doc


@router.get("/documentos/{doc_id}/traza")
def traza_documento(doc_id: str) -> list[dict]:
    _doc(doc_id, "doc_id")
    filas = db.query("SELECT id, doc_id, paso, entrada, salida, modelo, tokens, latencia_ms, timestamp, usuario "
                     "FROM trazas_ingesta WHERE doc_id = ? ORDER BY id", (doc_id,))
    for f in filas:
        f["entrada"] = _json(f["entrada"]) if f["entrada"] and f["entrada"][:1] in "{[" else f["entrada"]
        f["salida"] = _json(f["salida"]) if f["salida"] and f["salida"][:1] in "{[" else f["salida"]
    return filas


class Decision(BaseModel):
    decision: str
    usuario: str | None = None
    comentario: str | None = None


@router.post("/documentos/{doc_id}/decision")
def decision_documento(doc_id: str, body: Decision) -> dict:
    if body.decision not in DECISIONES:
        raise HTTPException(400, f"decision debe ser una de: {', '.join(DECISIONES)}")
    _doc(doc_id, "doc_id")
    try:
        return decidir(doc_id, body.decision, body.usuario or "web", body.comentario)
    except KeyError as e:
        raise HTTPException(404, f"Documento {doc_id} no encontrado") from e


@router.get("/discrepancias")
def listar_discrepancias(doc_id: str | None = None, tema: str | None = None, estado: str | None = None,
                         severidad: str | None = None) -> list[dict]:
    where, params = [], []
    if doc_id:
        where.append("(x.doc_a = ? OR x.doc_b = ?)")
        params += [doc_id, doc_id]
    if tema:
        where.append("x.tema = ?")
        params.append(tema)
    if estado:
        where.append("COALESCE(x.estado, 'abierta') = ?")
        params.append(estado)
    if severidad:
        where.append("x.severidad = ?")
        params.append(severidad)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return db.query(f"SELECT {COLS_DISC} {FROM_DISC}{w} ORDER BY x.id DESC", params)


class Revision(BaseModel):
    decision: str
    comentario: str | None = None


@router.post("/discrepancias/{disc_id}/revisar")
def revisar_discrepancia(disc_id: int, body: Revision) -> dict:
    if body.decision not in ("confirmada", "descartada"):
        raise HTTPException(400, "decision debe ser 'confirmada' o 'descartada'")
    if not db.query("SELECT 1 FROM discrepancias WHERE id = ?", (disc_id,)):
        raise HTTPException(404, f"Discrepancia {disc_id} no encontrada")
    db.execute("UPDATE discrepancias SET estado_revision = ?, comentario = ? WHERE id = ?",
               (body.decision, body.comentario, disc_id))
    return {"ok": True}
