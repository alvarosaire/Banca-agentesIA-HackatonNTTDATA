"""Agente de consultas normativas (frente B).

Flujo de `responder(pregunta, canal, usuario)`:
  1. Un Agent con tools `cons_*` busca fragmentos (vector store), artículos (SQLite), conflictos
     (tabla discrepancias) y versiones anteriores, y devuelve JSON {respuesta, tema, citas, advertencias}.
  2. En Python (determinista): se verifica que cada `cita_literal` exista en el texto del chunk citado,
     se descartan las que no, se agregan advertencias de conflictos/versiones reemplazadas y se calcula
     la confianza con umbrales por backend.
  3. Se guarda la fila en `consultas` y la traza (Run.meta) en `trazas`.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from typing import Any

from app.agents.agent import Agent, AgentSession
from app.agents.tools import tool
from app.agents.trace import store as run_store
from app.config import get_settings
from app.normas import db
from app.normas.vector import Hit, get_store

log = logging.getLogger("consultas")

SIN_RESPALDO = "No encontré una norma vigente que respalde esta respuesta."

# Umbrales de confianza sobre el mejor score (best_score) de la búsqueda de normas vigentes.
#   alta:  score >= U1, >= 1 cita verificada y sin conflictos
#   media: cita verificada pero U2 <= score < U1, o hay conflicto
#   sin_respaldo: sin cita verificada o score < U2
# El índice local usa similitud léxica (coseno de términos: valores bajos); Pinecone con rerank
# (bge-reranker-v2-m3) da probabilidades 0-1; Pinecone sin rerank, coseno denso.
UMBRALES = {
    "local": {"U1": 0.25, "U2": 0.12},
    "pinecone_rerank": {"U1": 0.50, "U2": 0.10},
    "pinecone": {"U1": 0.45, "U2": 0.25},
}

TOP_K = 20
RERANK_TOP_N = 5

# Estado por petición (las tools corren en el mismo hilo que la sesión del agente)
_ctx = threading.local()


def _hits_vistos() -> dict[str, dict[str, Any]]:
    if not hasattr(_ctx, "hits"):
        _ctx.hits = {}
    return _ctx.hits


def _scores_vigentes() -> list[float]:
    if not hasattr(_ctx, "scores"):
        _ctx.scores = []
    return _ctx.scores


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[\"'“”‘’«»]", "", t)
    return " ".join(t.split())


def _articulo(a: Any) -> str:
    s = str(a or "").strip()
    s = re.sub(r"(?i)^(art[ií]culo|art\.?)\s*", "", s)
    return s.strip(" .°º")


def _hit_dict(h: Hit) -> dict[str, Any]:
    m = h.metadata or {}
    return {
        "id": h.id, "score": round(h.score, 4),
        "rerank_score": None if h.rerank_score is None else round(h.rerank_score, 4),
        "best_score": round(h.best_score, 4),
        "doc_id": m.get("doc_id"), "nombre_norma": m.get("nombre_norma"),
        "articulo": m.get("articulo"), "version": m.get("version"), "estado": m.get("estado"),
        "texto": h.text,
    }


def _registrar(hits: list[dict[str, Any]]) -> None:
    vistos = _hits_vistos()
    for h in hits:
        vistos.setdefault(h["id"], h)


def _docs_sqlite() -> dict[str, dict[str, Any]]:
    """Estado actual de cada documento (SQLite es la fuente de verdad; el índice puede ir desfasado)."""
    try:
        return {d["doc_id"]: d for d in db.query("SELECT doc_id, nombre_norma, version, estado FROM documentos")}
    except Exception:
        return {}


def _buscar(consulta: str, solo_vigentes: bool) -> list[dict[str, Any]]:
    flt = {"estado": "vigente"} if solo_vigentes else None
    hits = get_store().search(consulta, top_k=TOP_K, filter=flt, rerank_top_n=RERANK_TOP_N)
    docs = _docs_sqlite()
    out = []
    for h in hits:
        d = _hit_dict(h)
        doc = docs.get(d.get("doc_id") or "")
        if doc:
            d.update({k: doc[k] for k in ("nombre_norma", "version", "estado") if doc.get(k)})
        if solo_vigentes and d.get("estado") != "vigente":
            continue
        out.append(d)
    _registrar(out)
    if solo_vigentes:
        _scores_vigentes().extend(h["best_score"] for h in out)
    return out


# ---------------------------------------------------------------------------- Tools
@tool(description="Busca fragmentos de normas (por defecto solo vigentes) relevantes a la consulta. "
                  "Devuelve id del fragmento, scores, doc_id, nombre_norma, articulo, version, estado y texto.",
      params={"consulta": "Pregunta o términos de búsqueda",
              "solo_vigentes": "true para buscar solo normas vigentes (recomendado)"})
def cons_buscar_normas(consulta: str, solo_vigentes: bool = True) -> dict:
    hits = _buscar(consulta, solo_vigentes)
    return {"n": len(hits), "hits": hits}


@tool(description="Obtiene el texto completo de un artículo de una norma desde la base documental.",
      params={"doc_id": "ID del documento, ej. SINT-02", "articulo": "Número de artículo, ej. 5"})
def cons_buscar_articulo(doc_id: str, articulo: str) -> dict:
    filas = db.query(
        "SELECT c.chunk_id, c.doc_id, c.articulo, c.texto, d.nombre_norma, d.version, d.estado "
        "FROM chunks c JOIN documentos d ON d.doc_id = c.doc_id WHERE c.doc_id = ? AND c.articulo = ? "
        "ORDER BY c.chunk_id", (doc_id.strip(), _articulo(articulo)))
    if not filas:
        return {"error": f"No se encontró el artículo {articulo} en {doc_id}"}
    hits = [{"id": f["chunk_id"], "score": None, "rerank_score": None, "best_score": None,
             "doc_id": f["doc_id"], "nombre_norma": f["nombre_norma"], "articulo": f["articulo"],
             "version": f["version"], "estado": f["estado"], "texto": f["texto"]} for f in filas]
    _registrar(hits)
    return {"n": len(hits), "fragmentos": hits}


def _conflictos(doc_ids: list[str]) -> list[dict[str, Any]]:
    ids = [d.strip() for d in doc_ids if d and d.strip()]
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return db.query(
        f"SELECT id, tipo, severidad, doc_a, chunk_a, cita_a, doc_b, chunk_b, cita_b, explicacion, estado_revision "
        f"FROM discrepancias WHERE estado_revision != 'descartada' AND tipo = 'contradice' "
        f"AND COALESCE(estado, 'abierta') != 'resuelta' "
        f"AND (doc_a IN ({ph}) OR doc_b IN ({ph})) ORDER BY id DESC", (*ids, *ids))


@tool(description="Consulta discrepancias registradas (contradicciones entre normas) que involucran a "
                  "los documentos indicados. Úsala siempre antes de responder.",
      params={"doc_ids": "IDs de documentos separados por coma, ej. 'SINT-02,SINT-03'"})
def cons_consultar_conflictos(doc_ids: str) -> dict:
    lista = [d for d in re.split(r"[,\s;]+", doc_ids or "") if d]
    filas = _conflictos(lista)
    return {"n": len(filas), "conflictos": filas}


@tool(description="Busca en TODAS las normas, incluidas reemplazadas o derogadas, para detectar "
                  "versiones anteriores que el usuario podría estar usando y advertirlo.",
      params={"consulta": "Pregunta o términos de búsqueda"})
def cons_buscar_versiones_anteriores(consulta: str) -> dict:
    hits = [h for h in _buscar(consulta, solo_vigentes=False) if h.get("estado") != "vigente"]
    return {"n": len(hits), "no_vigentes": hits}


TOOLS = ["cons_buscar_normas", "cons_buscar_articulo", "cons_consultar_conflictos",
         "cons_buscar_versiones_anteriores"]

ROLE = f"""Eres el asistente de normativa y políticas internas de un banco. Respondes consultas de
colaboradores (agencias, riesgos, cumplimiento) SOLO con base en fragmentos recuperados por tus herramientas.

Procedimiento obligatorio:
1. Llama a cons_buscar_normas con la pregunta (solo_vigentes=true).
2. Llama a cons_buscar_versiones_anteriores para detectar normas reemplazadas sobre el mismo tema.
3. Llama a cons_consultar_conflictos con los doc_id de los fragmentos que vas a citar.
4. Si necesitas el texto completo de un artículo, usa cons_buscar_articulo.

Reglas:
- Responde solo con lo que dicen los fragmentos recuperados. No uses conocimiento propio.
- Cita documento, artículo y versión (ej. "Política de Crédito de Consumo v2, Art. 5").
- Si existe una versión reemplazada con un valor distinto, adviértelo (indica que ya no está vigente).
- Si hay un conflicto entre normas que afecte a los artículos que citas, adviértelo en "advertencias" y
  explica ambas posiciones. No menciones conflictos sobre otros temas.
- Si ningún fragmento vigente responde la pregunta, la respuesta debe ser exactamente:
  "{SIN_RESPALDO}" y "citas" debe ser [].
- Cada "cita_literal" debe ser un texto COPIADO TAL CUAL del fragmento (una frase corta, sin el prefijo
  entre corchetes), y "chunk_id" el id exacto del fragmento.

Tu respuesta final debe ser SOLO un objeto JSON (sin texto adicional) con esta forma:
{{"respuesta": "...", "tema": "<uno de: {', '.join(db.TEMAS)}>",
  "citas": [{{"chunk_id": "...", "cita_literal": "..."}}], "advertencias": ["..."]}}"""

AGENTE = Agent(name="consultas", role=ROLE, tools=TOOLS, temperature=0, max_iterations=8)


# ---------------------------------------------------------------------------- Post-proceso
def _parse_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    t = re.sub(r"```(?:json)?", "", text).strip()
    for cand in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t else ""):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return {"respuesta": text.strip()}


def _umbrales() -> dict[str, float]:
    st = get_store()
    if st.backend == "pinecone":
        return UMBRALES["pinecone_rerank"] if RERANK_TOP_N else UMBRALES["pinecone"]
    return UMBRALES["local"]


def _texto_chunk(chunk_id: str) -> dict[str, Any] | None:
    h = _hits_vistos().get(chunk_id)
    if h:
        return h
    filas = db.query(
        "SELECT c.chunk_id, c.doc_id, c.articulo, c.texto, d.nombre_norma, d.version, d.estado "
        "FROM chunks c JOIN documentos d ON d.doc_id = c.doc_id WHERE c.chunk_id = ?", (chunk_id,))
    if not filas:
        return None
    f = filas[0]
    return {"id": f["chunk_id"], "doc_id": f["doc_id"], "nombre_norma": f["nombre_norma"],
            "articulo": f["articulo"], "version": f["version"], "estado": f["estado"], "texto": f["texto"]}


def _verificar_citas(citas: Any) -> list[dict[str, Any]]:
    out, vistos = [], set()
    if not isinstance(citas, list):
        return out
    for c in citas:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("chunk_id") or c.get("id") or "").strip()
        lit = str(c.get("cita_literal") or "").strip().strip(".…")
        if not cid or len(_norm(lit)) < 8:
            continue
        ch = _texto_chunk(cid)
        if not ch:
            continue
        # tolera "..." intermedios: cada tramo debe estar en el texto
        partes = [p for p in re.split(r"\.\.\.|…", lit) if _norm(p)]
        texto_n = _norm(ch["texto"])
        if not partes or not all(_norm(p).strip(" .,;:") in texto_n for p in partes):
            log.info("Cita descartada (no literal) %s: %s", cid, lit[:80])
            continue
        if cid in vistos:
            continue
        vistos.add(cid)
        out.append({"chunk_id": cid, "doc_id": ch.get("doc_id"), "nombre_norma": ch.get("nombre_norma"),
                    "articulo": ch.get("articulo"), "version": ch.get("version"),
                    "estado": ch.get("estado"), "texto": ch.get("texto"), "cita_literal": lit})
    return out


def _advertencias_deterministas(citas: list[dict[str, Any]], previas: list[str]) -> tuple[list[str], bool]:
    """Agrega avisos de conflictos y de versiones reemplazadas que el LLM haya omitido."""
    adv = list(previas)
    texto_prev = _norm(" ".join(previas))
    docs = _docs_sqlite()

    def llm_aviso(otro: str) -> bool:
        nombre = _norm((docs.get(otro) or {}).get("nombre_norma") or otro)
        return any(("conflict" in a or "contradic" in a) and (_norm(otro) in a or nombre in a)
                   for a in map(_norm, previas))
    doc_ids = sorted({c["doc_id"] for c in citas if c.get("doc_id")})
    chunk_ids = {c["chunk_id"] for c in citas}
    conflictos = []
    for f in _conflictos(doc_ids):
        # a nivel de chunk si la discrepancia lo indica; si no, a nivel de documento
        if (f.get("chunk_a") or f.get("chunk_b")) and not ({f.get("chunk_a"), f.get("chunk_b")} & chunk_ids):
            continue
        conflictos.append(f)
        otro = f["doc_b"] if f["doc_a"] in doc_ids else f["doc_a"]
        if not llm_aviso(otro or ""):
            adv.append(f"Conflicto normativo entre {f['doc_a']} y {f['doc_b']}: {f.get('explicacion') or ''}".strip())
    # versiones reemplazadas de las normas citadas (o que aparecieron en la búsqueda sin filtro)
    citadas = {(c.get("nombre_norma"), _articulo(c.get("articulo"))) for c in citas}
    nombres = {n for n, _ in citadas}
    for h in _hits_vistos().values():
        relacionada = h.get("nombre_norma") in nombres and (
            (h.get("nombre_norma"), _articulo(h.get("articulo"))) in citadas or not h.get("articulo"))
        if h.get("estado") in ("reemplazada", "derogada") and relacionada and h.get("doc_id"):
            _ctx.obsoleta = True  # KPI: versión no vigente involucrada en la respuesta
        if (h.get("estado") in ("reemplazada", "derogada") and relacionada and h.get("doc_id")
                and not ("reemplaz" in texto_prev or "derog" in texto_prev or "anterior" in texto_prev)):
            if not any(h["doc_id"] in a for a in adv):
                adv.append(f"{h.get('nombre_norma')} v{h.get('version')} ({h['doc_id']}) está {h['estado']}: "
                           f"no la uses como referencia.")
    return adv, bool(conflictos)


def _confianza(top: float, n_citas: int, conflicto: bool) -> str:
    u = _umbrales()
    if n_citas == 0 or top < u["U2"]:
        return "sin_respaldo"
    if top >= u["U1"] and not conflicto:
        return "alta"
    return "media"


# ---------------------------------------------------------------------------- Métricas (KPIs)
def metricas(n_propuestas: int, verificadas: list[dict[str, Any]], top: float | None,
             conflicto: bool, obsoleta: bool) -> dict[str, Any]:
    """Valores calculados por el código que decide (no por texto libre). Ver docs/KPIS.md."""
    n_ver = len(verificadas)
    if n_ver:
        scores = [(_hits_vistos().get(c["chunk_id"]) or {}).get("best_score") for c in verificadas]
        scores = [x for x in scores if x is not None]
        best = max(scores) if scores else (top or 0.0)  # cita leída por artículo: score de la búsqueda
        score = round(best * (n_ver / max(n_propuestas, n_ver)), 4)
    else:
        score = 0.0
    if n_ver == 0:
        motivo = "no_encontro"
    elif top is not None and top < _umbrales()["U2"]:
        motivo = "score_bajo"
    else:
        motivo = None
    return {"n_citas_propuestas": n_propuestas, "n_citas_verificadas": n_ver, "confianza_score": score,
            "advertencia_conflicto": int(bool(conflicto)), "advertencia_obsoleta": int(bool(obsoleta)),
            "motivo_sin_cobertura": motivo}


def guardar_consulta(tipo: str, canal: str, usuario: str, pregunta: str, tema: str, respuesta: str,
                     confianza: str, top: float | None, citas: list[dict[str, Any]], advertencias: list[str],
                     run_id: str, latencia: int, m: dict[str, Any]) -> int:
    return db.execute(
        "INSERT INTO consultas (canal, usuario, pregunta, tema, respuesta, confianza, top_score, cubierta,"
        " citas_json, advertencias_json, run_id, latencia_ms, created_at, tipo, n_citas_propuestas,"
        " n_citas_verificadas, confianza_score, advertencia_conflicto, advertencia_obsoleta, motivo_sin_cobertura)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (canal, usuario, pregunta, tema, respuesta, confianza, top, int(confianza != "sin_respaldo"),
         json.dumps(citas, ensure_ascii=False), json.dumps(advertencias, ensure_ascii=False),
         run_id, latencia, db.now(), tipo, m["n_citas_propuestas"], m["n_citas_verificadas"],
         m["confianza_score"], m["advertencia_conflicto"], m["advertencia_obsoleta"], m["motivo_sin_cobertura"]))


# ---------------------------------------------------------------------------- API pública
def responder(pregunta: str, canal: str = "web", usuario: str = "anonimo") -> dict[str, Any]:
    t0 = time.perf_counter()
    _ctx.hits, _ctx.scores, _ctx.obsoleta = {}, [], False
    run = run_store.create("normativa", pregunta)
    run.meta = {"agente": "consultas", "canal": canal, "usuario": usuario,
                "modelo": get_settings().llm_model}
    session = AgentSession(AGENTE, run, pregunta)
    try:
        session.start()
    except Exception as e:  # nunca romper el endpoint
        log.exception("Error en agente de consultas")
        session.status, session.output = "failed", str(e)

    data = _parse_json(session.output or "") if session.status == "done" else {}
    if session.status != "done":
        run.add_step("consultas", "error", f"Agente no terminó: {session.output}")

    # Búsqueda determinista con la pregunta original: score objetivo aunque el agente reformule
    _buscar(pregunta, solo_vigentes=True)
    top = max(_scores_vigentes() or [0.0])

    propuestas = [c for c in data["citas"] if isinstance(c, dict)] if isinstance(data.get("citas"), list) else []
    citas = _verificar_citas(data.get("citas"))
    adv_llm = [str(a) for a in (data.get("advertencias") or []) if str(a).strip()
               and not re.match(r"(?i)^\s*no se (registr|encontr|detect|identific)", str(a))]
    no_vigentes = [c for c in citas if c.get("estado") != "vigente"]
    citas = [c for c in citas if c.get("estado") == "vigente"]
    for c in no_vigentes:  # una norma no vigente nunca respalda la respuesta
        adv_llm.append(f"Se descartó como respaldo {c.get('nombre_norma')} v{c.get('version')} Art. "
                       f"{c.get('articulo')} ({c.get('doc_id')}) porque está {c.get('estado')}.")
    advertencias, conflicto = _advertencias_deterministas(citas, adv_llm)
    confianza = _confianza(top, len(citas), conflicto)
    m = metricas(len(propuestas), citas, top, conflicto, bool(no_vigentes) or bool(getattr(_ctx, "obsoleta", False)))

    respuesta = str(data.get("respuesta") or "").strip()
    if confianza == "sin_respaldo" or not respuesta:
        confianza = "sin_respaldo"
        respuesta, citas = SIN_RESPALDO, []
        advertencias = adv_llm
        m["advertencia_conflicto"] = 0  # el aviso determinista de conflicto no se entrega
        m["advertencia_obsoleta"] = int(bool(no_vigentes))
        m["motivo_sin_cobertura"] = m["motivo_sin_cobertura"] or "no_encontro"
    tema = data.get("tema") if data.get("tema") in db.TEMAS else "otros"

    latencia = int((time.perf_counter() - t0) * 1000)
    run.add_step("consultas", "verificacion",
                 f"confianza={confianza} top_score={top:.3f} citas_verificadas={len(citas)} "
                 f"conflicto={conflicto} umbrales={_umbrales()}",
                 citas=[c["chunk_id"] for c in citas], top_score=top)
    run.status = "completed" if session.status == "done" else "failed"
    run.output = respuesta
    run.meta["latencia_ms"] = latencia
    run_store.save(run)

    guardar_consulta("consulta", canal, usuario, pregunta, tema, respuesta, confianza, top, citas, advertencias,
                     run.id, latencia, m)

    return {"respuesta": respuesta, "confianza": confianza, "cubierta": confianza != "sin_respaldo",
            "citas": citas, "advertencias": advertencias, "tema": tema, "top_score": round(top, 4),
            "run_id": run.id, "latencia_ms": latencia}
