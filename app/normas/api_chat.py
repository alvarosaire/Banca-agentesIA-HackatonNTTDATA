"""Endpoints del chat de casos (frente C, backend).

Contrato en docs/CONTRATOS.md (sección Chat de casos).
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.normas import db
from app.normas.areas import AREAS, area_valida, de_json

log = logging.getLogger("api_chat")
router = APIRouter(tags=["chat"])

UPLOADS = db.ROOT / "data" / "uploads" / "casos"
CASOS_DIR = db.ROOT / "corpus" / "casos"
EXT_OK = {".pdf", ".docx", ".txt", ".md"}
MAX_BYTES = 10 * 1024 * 1024


@router.post("/chat")
async def chat(session_id: str = Form(...), mensaje: str = Form(""),
               archivo: UploadFile | None = File(None), x_usuario: str | None = Header(None),
               x_area: str | None = Header(None)) -> dict:
    from app.normas.casos import chat as chat_casos  # import diferido: registra las tools al usarse
    ruta: Path | None = None
    if archivo is not None and archivo.filename:
        ext = Path(archivo.filename).suffix.lower()
        if ext not in EXT_OK:
            raise HTTPException(400, f"Formato no soportado: {ext} (usa PDF, DOCX o TXT)")
        contenido = await archivo.read()
        if len(contenido) > MAX_BYTES:
            raise HTTPException(413, "Archivo demasiado grande (máx. 10 MB)")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        nombre = re.sub(r"[^A-Za-z0-9._-]", "_", Path(archivo.filename).name)[-80:]
        ruta = UPLOADS / f"{int(time.time() * 1000)}_{nombre}"
        ruta.write_bytes(contenido)
    if not (mensaje or "").strip() and ruta is None:
        raise HTTPException(400, "Envía un mensaje o un archivo")
    if x_usuario and not session_id.startswith(f"{x_usuario}:"):
        session_id = f"{x_usuario}:{session_id}"  # el usuario autenticado queda en la traza
    out = await run_in_threadpool(chat_casos, session_id, mensaje, ruta)
    try:
        await run_in_threadpool(enriquecer_areas, out, area_usuario(x_area, x_usuario))
    except Exception:  # el enriquecimiento nunca debe tumbar la respuesta
        log.exception("No se pudo enriquecer la respuesta con áreas")
        out.setdefault("area_usuario", None)
    return out


# ---------------------------------------------------------------------------- áreas (post-proceso)
def area_usuario(x_area: str | None, x_usuario: str | None) -> str | None:
    """Header X-Area (puesto por /auth/check) o, si no viene, el área registrada del usuario."""
    if x_area:
        crudo = unquote(x_area)  # /auth/check la envía percent-encoded (cabeceras ASCII)
        try:  # un cliente pudo mandar UTF-8 crudo que Starlette leyó como latin-1
            crudo = crudo.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        a = area_valida(crudo) or area_valida(x_area)
        if a:
            return a
    if x_usuario:
        from app.normas import usuarios
        u = usuarios.obtener(x_usuario)
        if u and u.get("area"):
            return u["area"]
    return None


def _nota(area: str | None, areas: list[str]) -> str | None:
    if not area or not areas or area in areas:
        return None
    return f"Esta norma no es de tu área ({area}), pero te aplica."


def enriquecer_areas(out: dict[str, Any], area: str | None) -> dict[str, Any]:
    """Agrega area_usuario, areas_aplica/nota_area por cita y dictamen.areas_a_coordinar; lo persiste."""
    dictamen = out.get("dictamen") if isinstance(out.get("dictamen"), dict) else None
    evals = [e for e in (dictamen or {}).get("evaluaciones") or [] if isinstance(e, dict) and e.get("doc_id")]
    citas = [c for c in out.get("citas") or [] if isinstance(c, dict)]
    ids = sorted({x["doc_id"] for x in citas + evals if x.get("doc_id")})
    mapa: dict[str, list[str]] = {}
    if ids:
        filas = db.query(f"SELECT doc_id, areas_aplica FROM documentos WHERE doc_id IN ({','.join('?' * len(ids))})",
                         ids)
        mapa = {f["doc_id"]: de_json(f["areas_aplica"]) for f in filas}
    for x in citas + evals:
        lista = mapa.get(x.get("doc_id"), []) if x.get("doc_id") else []
        x["areas_aplica"] = lista
        x["nota_area"] = _nota(area, lista)
    out["area_usuario"] = area
    if dictamen is not None:
        union = {a for x in evals + citas for a in x.get("areas_aplica") or []}
        dictamen["areas_a_coordinar"] = [a for a in AREAS if a in union]  # orden de la lista cerrada
    # el historial muestra lo mismo que la respuesta; la consulta guarda el área de quien preguntó
    sid = out.get("session_id")
    if sid:
        db.execute("UPDATE chat_mensajes SET payload_json = ? WHERE id = (SELECT MAX(id) FROM chat_mensajes "
                   "WHERE session_id = ? AND rol = 'agente')", (json.dumps(out, ensure_ascii=False), sid))
    if out.get("run_id"):
        db.execute("UPDATE consultas SET area_usuario = ? WHERE run_id = ?", (area, out["run_id"]))
    return out


@router.get("/chat/ejemplos")
def ejemplos() -> list[dict]:
    out = []
    for p in sorted(CASOS_DIR.glob("*.txt")):
        texto = p.read_text(encoding="utf-8").strip()
        titulo = texto.splitlines()[0].strip() if texto else p.stem
        out.append({"id": p.stem, "titulo": titulo, "texto": texto})
    return out


@router.get("/chat/{session_id}")
def historial(session_id: str, x_usuario: str | None = Header(None)) -> list[dict]:
    if x_usuario and not session_id.startswith(f"{x_usuario}:"):
        session_id = f"{x_usuario}:{session_id}"
    from app.normas.casos import historial as hist
    return hist(session_id)


DESCARGABLES = ("vigente", "reemplazada", "derogada", "por_entrar_en_vigencia")


@router.get("/chat/norma/{doc_id}/archivo")
def descargar_norma(doc_id: str):
    """Descarga el documento original de una norma citada (solo normas publicadas, no pendientes ni rechazadas)."""
    from fastapi.responses import FileResponse
    filas = db.query("SELECT archivo, estado, publicado FROM documentos WHERE doc_id = ?", (doc_id,))
    if not filas or not filas[0]["archivo"]:
        raise HTTPException(404, "Norma no encontrada")
    d = filas[0]
    if d["estado"] not in DESCARGABLES or (d["estado"] == "vigente" and not d["publicado"]):
        raise HTTPException(403, "Esta norma no está publicada")
    ruta = Path(d["archivo"])
    ruta = (ruta if ruta.is_absolute() else db.ROOT / ruta).resolve()
    if not ruta.is_file() or db.ROOT.resolve() not in ruta.parents:
        raise HTTPException(404, "Archivo no disponible")
    nombre = re.sub(r"^\d{10,}_", "", ruta.name)  # quita el prefijo de tiempo de las subidas repetidas
    return FileResponse(ruta, filename=nombre)
