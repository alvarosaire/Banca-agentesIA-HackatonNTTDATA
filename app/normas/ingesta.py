"""Agente 1 — Ingesta y validación de normas (pipeline).

    res = ingerir("normas_demo/09_man-cr-2025-004.md", usuario="seed")   # síncrono, devuelve resumen
    ctx = recibir(path); procesar(ctx)                                     # en dos pasos (la API usa un hilo)

Pasos (cada uno deja una fila en trazas_ingesta):
  recepcion      archivo, sha256 (si ya existe: duplicado=true, no se reindexa)
  metadatos      frontmatter si existe; si no, LLM con confianza 0-1 por campo; faltante = null
                 + requiere_confirmacion. Valida fechas. Estado no declarado -> inferido (estado_inferido=1).
                 Áreas de aplicación (resolver_areas): ficha/frontmatter o UNA llamada corta al LLM (lista cerrada,
                 areas_requiere_confirmacion aparte); van como `areas_aplica` en cada chunk del índice.
  edicion_areas  corrección humana de las áreas (editar_areas, PUT /api/documentos/{id}/areas).
  chunks         por artículo / disposición final, ids determinísticos {codigo}_{version}_art-N.
                 Poco texto o sin artículos -> estado_ingesta=requiere_revision, NO se indexa.
  afirmaciones / candidatos / comparacion / discrepancia_*  -> app/normas/validacion.py
  publicacion    sin discrepancias ALTA bloqueantes -> publicado=1 y se aplican los reemplazos.
                 Con ALTA -> estado pendiente_revision, publicado=0 (el Agente 2 no la usa).
  decision_usuario  aprobar_publicar | publicar_igual | rechazar (decidir()).
Nada se borra: las normas reemplazadas quedan con estado 'reemplazada' en SQLite y en el índice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.normas import areas as areas_mod
from app.normas import db
from app.normas.chunking import chunkear
from app.normas.parsing import FM_RE, parsear, separar_frontmatter
from app.normas.vector import fecha_int, get_store

log = logging.getLogger("ingesta")

CORPUS_META = db.ROOT / "corpus" / "metadatos.json"
CAMPOS = ["codigo", "nombre", "entidad", "fecha_emision", "fecha_vigencia", "estado", "version", "reemplaza_a"]
REQUERIDOS = ["codigo", "nombre", "entidad", "fecha_emision", "fecha_vigencia", "version"]
ESTADOS = ["vigente", "derogada", "reemplazada", "por_entrar_en_vigencia"]
UMBRAL_CONFIANZA = 0.7
# Entidades reguladoras -> norma externa (si no: interna)
REGULADOR_RE = re.compile(
    r"\b(SBS|SMV|BCRP|UIF|SUNAT|MEF|INDECOPI|Congreso|Superintendencia|Banco Central de Reserva|Ministerio|"
    r"Poder Ejecutivo|Presidencia de la Rep[úu]blica|Contralor[íi]a|Tribunal Constitucional|"
    r"Unidad de Inteligencia Financiera)\b", re.IGNORECASE)
CODIGO_RE = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z]{2,6})*-\d{4}-\d{2,4}\b")
DEROGA_RE = re.compile(r"der[oó]g\w*|d[ée]jese\s+sin\s+efecto|d[ée]janse\s+sin\s+efecto|quedan?\s+sin\s+efecto",
                       re.IGNORECASE)
MIN_CHARS = 60  # caracteres sin espacios (sin la ficha) por debajo de los cuales no se indexa;
# una circular de un solo artículo corto es válida (p. ej. CI-2025-013, 117 caracteres)

# Un documento a la vez en la fase de comparación/publicación (evita carreras entre subidas simultáneas)
LOCK = threading.Lock()


class ArchivoRechazado(ValueError):
    """Archivo que nunca debe indexarse (p. ej. la guía interna de la demo)."""


def es_archivo_prohibido(nombre: str) -> bool:
    n = Path(nombre).name.upper()
    return n.startswith("00_GUIA") or "NO_SUBIR" in n


def sha256_de(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def doc_por_sha(sha: str) -> dict[str, Any] | None:
    rows = db.query("SELECT * FROM documentos WHERE sha256 = ?", (sha,))
    return rows[0] if rows else None


def doc_por_codigo(codigo: str) -> dict[str, Any] | None:
    rows = db.query("SELECT * FROM documentos WHERE codigo = ? OR doc_id = ? ORDER BY created_at DESC",
                    (codigo, codigo))
    return rows[0] if rows else None


def metadatos_corpus(nombre_archivo: str) -> dict[str, Any] | None:
    """Metadatos conocidos del corpus antiguo de demo (corpus/metadatos.json)."""
    try:
        data = json.loads(CORPUS_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m = data.get(Path(nombre_archivo).name)
    return dict(m) if m else None


def tipo_fuente_de(entidad: str | None) -> str:
    return "externa" if entidad and REGULADOR_RE.search(entidad) else "interna"


def fecha_ref(d: dict[str, Any]) -> tuple[str, str]:
    """Clave de antigüedad: (fecha_vigencia, fecha_emision); prevalece la mayor."""
    return (d.get("fecha_vigencia") or "", d.get("fecha_emision") or "")


def _set(doc_id: str, **campos: Any) -> None:
    if not campos:
        return
    cols = ", ".join(f"{k} = ?" for k in campos)
    db.execute(f"UPDATE documentos SET {cols} WHERE doc_id = ?", (*campos.values(), doc_id))


def etapa(doc_id: str, nombre: str, mensaje: str | None = None) -> None:
    _set(doc_id, etapa=nombre, mensaje_ingesta=mensaje)


# ---------------------------------------------------------------------------- utilidades LLM
def json_tolerante(texto: str) -> Any:
    texto = re.sub(r"^```(json)?|```$", "", (texto or "").strip(), flags=re.M).strip()
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, texto, re.S)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except ValueError:
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
            except ValueError:
                continue
    return {}


def llm_json(prompt: str, doc_id: str | None, paso: str, usuario: str | None,
             entrada_traza: Any = None) -> tuple[Any, dict[str, Any]]:
    """Una llamada al LLM que debe devolver JSON. Registra modelo, tokens y latencia en trazas_ingesta."""
    from app.llm_client import get_llm

    t0 = time.perf_counter()
    info: dict[str, Any] = {"modelo": get_settings().llm_model, "tokens": None, "latencia_ms": None}
    try:
        resp = get_llm().chat([{"role": "user", "content": prompt}], temperature=0)
        info["latencia_ms"] = int((time.perf_counter() - t0) * 1000)
        if resp.usage:
            info["tokens"] = resp.usage.get("total_tokens")
        data = json_tolerante(resp.content)
        info["raw"] = resp.content
        return data, info
    except Exception as e:
        info["latencia_ms"] = int((time.perf_counter() - t0) * 1000)
        info["error"] = str(e)
        log.exception("LLM falló en %s (%s)", paso, doc_id)
        db.traza_ingesta(doc_id, f"{paso}_error", entrada_traza, {"error": str(e)}, info["modelo"],
                         None, info["latencia_ms"], usuario)
        return {}, info


def _fecha(v: Any) -> str | None:
    if not v:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(v))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


# ---------------------------------------------------------------------------- metadatos
def _desde_frontmatter(fm: dict[str, Any]) -> dict[str, Any]:
    alias = {"codigo": ["codigo", "código", "doc_id", "code"], "nombre": ["nombre_norma", "nombre", "titulo"],
             "entidad": ["entidad_emisora", "entidad", "emisor"], "fecha_emision": ["fecha_emision"],
             "fecha_vigencia": ["fecha_vigencia"], "estado": ["estado"], "version": ["version", "versión"],
             "reemplaza_a": ["reemplaza_a", "reemplaza"]}
    out: dict[str, Any] = {}
    for campo, claves in alias.items():
        for k in claves:
            if k in fm:
                out[campo] = fm[k]
                break
    return out


def extraer_metadatos_llm(texto: str, faltan: list[str], doc_id: str | None, usuario: str | None) -> tuple[dict, dict]:
    prompt = (
        "Extrae metadatos de esta norma o política bancaria. NO inventes: si un dato no aparece explícito en el "
        "texto, devuelve valor null y confianza 0. Responde SOLO un objeto JSON cuyas claves sean exactamente "
        f"{json.dumps(faltan)} y cada valor sea {{\"valor\": ..., \"confianza\": número 0-1}}.\n"
        "- codigo: identificador oficial de la norma (ej. RES-SINT-2024-001, MAN-CR-2025-004).\n"
        "- nombre: título de la norma. entidad: quién la emite.\n"
        "- fecha_emision / fecha_vigencia en AAAA-MM-DD.\n"
        f"- estado: uno de {ESTADOS} SOLO si el texto lo declara; si no, null.\n"
        "- version: texto (ej. \"1.0\"). reemplaza_a: código de la norma que esta reemplaza o deroga, si lo dice.\n\n"
        f"Texto (inicio):\n{texto[:5000]}"
    )
    data, info = llm_json(prompt, doc_id, "metadatos", usuario)
    vals, conf = {}, {}
    if isinstance(data, dict):
        for c in faltan:
            v = data.get(c)
            if isinstance(v, dict):
                val, cf = v.get("valor"), v.get("confianza")
            else:
                val, cf = v, None
            val = None if val in ("", "null", "None") else val
            try:
                cf = max(0.0, min(1.0, float(cf))) if cf is not None else (0.5 if val else 0.0)
            except (TypeError, ValueError):
                cf = 0.5 if val else 0.0
            vals[c] = val
            conf[c] = round(cf, 2) if val is not None else 0.0
    return {"valores": vals, "confianza": conf}, info


def resolver_metadatos(fm: dict[str, Any], provistos: dict[str, Any] | None, texto: str, sha: str,
                       doc_id: str | None, usuario: str | None) -> dict[str, Any]:
    """-> {meta, confianza, fuente, requiere_confirmacion, estado_inferido, advertencias}"""
    meta: dict[str, Any] = {c: None for c in CAMPOS}
    conf: dict[str, float] = {}
    fuente: dict[str, str] = {}
    origen = None
    if provistos:  # compatibilidad: metadatos dados por el llamador (corpus antiguo)
        p = dict(provistos)
        p.setdefault("codigo", p.get("doc_id"))
        p.setdefault("nombre", p.get("nombre_norma"))
        for c in CAMPOS:
            if p.get(c) not in (None, ""):
                meta[c], conf[c], fuente[c] = p[c], 1.0, "provisto"
        origen = "provisto"
    elif fm:
        f = _desde_frontmatter(fm)
        for c in CAMPOS:
            if c in f:  # declarado en el frontmatter (aunque vacío: reemplaza_a vacío = no reemplaza)
                meta[c] = f[c]
                conf[c], fuente[c] = 1.0, "frontmatter"
        origen = "frontmatter"
    faltan = [c for c in CAMPOS if meta[c] is None and fuente.get(c) is None]
    llm_info = None
    if faltan and len(re.sub(r"\s", "", texto)) < 50:  # sin texto: no hay nada que extraer
        for c in faltan:
            conf[c], fuente[c] = 0.0, "sin_texto"
        faltan = []
    if faltan:
        res, llm_info = extraer_metadatos_llm(texto, faltan, doc_id, usuario)
        for c in faltan:
            v = res["valores"].get(c)
            meta[c] = v
            conf[c] = res["confianza"].get(c, 0.0)
            fuente[c] = "llm"
        origen = origen or "llm"

    adv: list[str] = []
    meta["fecha_emision"] = _fecha(meta["fecha_emision"])
    meta["fecha_vigencia"] = _fecha(meta["fecha_vigencia"])
    meta["version"] = str(meta["version"]).strip() if meta["version"] not in (None, "") else None
    meta["codigo"] = str(meta["codigo"]).strip() if meta["codigo"] else None
    meta["reemplaza_a"] = str(meta["reemplaza_a"]).strip() if meta["reemplaza_a"] else None
    if meta["reemplaza_a"] and meta["reemplaza_a"] == meta["codigo"]:
        meta["reemplaza_a"] = None
    fe, fv = meta["fecha_emision"], meta["fecha_vigencia"]
    if fe and fv and fv < fe:
        adv.append(f"fecha_vigencia ({fv}) es anterior a fecha_emision ({fe}): revisar")
    estado = (meta["estado"] or "").strip().lower() or None
    if estado and estado not in ESTADOS:
        adv.append(f"estado declarado '{estado}' no reconocido: se infiere por fechas")
        estado = None
    estado_inferido = 0
    if not estado:
        estado_inferido = 1
        estado = "por_entrar_en_vigencia" if fv and fv > date.today().isoformat() else "vigente"
        conf["estado"] = conf.get("estado") or 0.0
        fuente["estado"] = "inferido_por_fechas"
    meta["estado"] = estado
    faltantes = [c for c in REQUERIDOS if not meta.get(c)]
    baja_conf = [c for c in REQUERIDOS if meta.get(c) and conf.get(c, 0) < UMBRAL_CONFIANZA]
    requiere = bool(faltantes or baja_conf or adv)
    return {"meta": meta, "confianza": conf, "fuente": fuente, "origen": origen, "faltantes": faltantes,
            "baja_confianza": baja_conf, "requiere_confirmacion": int(requiere),
            "estado_inferido": estado_inferido, "advertencias": adv, "llm": llm_info}


# ---------------------------------------------------------------------------- áreas de aplicación
def resolver_areas(fm: dict[str, Any], fm_origen: str | None, texto: str, nombre: str | None,
                   doc_id: str | None, usuario: str | None) -> dict[str, Any]:
    """-> {areas_aplica, areas_fuente, areas_confianza, areas_requiere_confirmacion, descartadas, llm}

    Declaradas (fila "Áreas de aplicación" de la ficha o `areas_aplica` del frontmatter) -> confianza 1.0.
    Si no: UNA llamada corta al LLM restringida a la lista cerrada -> requiere confirmación humana.
    Nunca se devuelve un área fuera de AREAS. No afecta `requiere_confirmacion` del documento.
    """
    declarado = next((fm[k] for k in ("areas_aplica", "areas", "areas_de_aplicacion") if fm.get(k)), None)
    descartadas: list[str] = []
    if declarado:
        lista, descartadas = areas_mod.normalizar_con_descartes(declarado)
        if lista:
            return {"areas_aplica": lista, "areas_fuente": fm_origen or "frontmatter", "areas_confianza": 1.0,
                    "areas_requiere_confirmacion": int(bool(descartadas)), "declarado": declarado,
                    "descartadas": descartadas, "llm": None}
    if len(re.sub(r"\s", "", texto or "")) < 50:  # sin texto: no se consulta al LLM
        return {"areas_aplica": [], "areas_fuente": "llm", "areas_confianza": 0.0,
                "areas_requiere_confirmacion": 1, "declarado": declarado, "descartadas": descartadas,
                "llm": None, "motivo": "sin texto: no se consultó al LLM"}
    prompt = (
        "¿A qué áreas de un banco aplica esta norma o política (quiénes deben cumplirla o conocerla)? "
        f"Elige SOLO de esta lista cerrada: {json.dumps(areas_mod.AREAS, ensure_ascii=False)}. "
        "No inventes áreas fuera de la lista. Responde SOLO un objeto JSON "
        '{"areas": ["..."], "confianza": número 0-1}.\n\n'
        f"Título: {nombre or ''}\nTexto (inicio):\n{(texto or '')[:4000]}"
    )
    data, info = llm_json(prompt, doc_id, "metadatos", usuario, {"campo": "areas_aplica"})
    lista, desc_llm = areas_mod.normalizar_con_descartes(data.get("areas") if isinstance(data, dict) else None)
    try:
        cf = max(0.0, min(1.0, float(data.get("confianza")))) if isinstance(data, dict) and lista else 0.0
    except (TypeError, ValueError):
        cf = 0.5 if lista else 0.0
    llm = {k: info.get(k) for k in ("modelo", "tokens", "latencia_ms", "error") if info.get(k) is not None}
    return {"areas_aplica": lista, "areas_fuente": "llm", "areas_confianza": round(cf, 2),
            "areas_requiere_confirmacion": 1, "declarado": declarado, "descartadas": descartadas + desc_llm,
            "llm": llm}


def editar_areas(doc_id: str, nuevas: list[str], usuario: str | None = None) -> dict[str, Any]:
    """Corrección humana de las áreas: BD (fuente manual) + re-upsert de los chunks con el mismo estado."""
    lista, descartadas = areas_mod.normalizar_con_descartes(nuevas)
    if descartadas:
        raise ValueError(f"Áreas no válidas: {descartadas}. Válidas: {areas_mod.AREAS}")
    rows = db.query("SELECT doc_id, estado, areas_aplica, areas_fuente, areas_confianza, "
                    "areas_requiere_confirmacion FROM documentos WHERE doc_id = ?", (doc_id,))
    if not rows:
        raise KeyError(doc_id)
    antes = rows[0]
    with LOCK:
        _set(doc_id, areas_aplica=json.dumps(lista, ensure_ascii=False), areas_fuente="manual", areas_confianza=1.0,
             areas_requiere_confirmacion=0)
        n = reindexar(doc_id)  # mismo estado que ya tenía (records_de usa documentos.estado)
        db.traza_ingesta(doc_id, "edicion_areas",
                         {"antes": {"areas_aplica": areas_mod.de_json(antes["areas_aplica"]),
                                    "areas_fuente": antes["areas_fuente"], "areas_confianza": antes["areas_confianza"],
                                    "areas_requiere_confirmacion": antes["areas_requiere_confirmacion"]}},
                         {"despues": {"areas_aplica": lista, "areas_fuente": "manual", "areas_confianza": 1.0,
                                      "areas_requiere_confirmacion": 0},
                          "chunks_reindexados": n, "estado_indice": antes["estado"]}, usuario=usuario)
    return {"ok": True, "areas_aplica": lista}


# ---------------------------------------------------------------------------- vector store
def temas_por_chunk(doc_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in db.query("SELECT chunk_id, tema FROM afirmaciones WHERE doc_id = ? ORDER BY id", (doc_id,)):
        if r["chunk_id"] and r["tema"] and r["tema"] not in out.setdefault(r["chunk_id"], []):
            out[r["chunk_id"]].append(r["tema"])
    return out


def records_de(doc_id: str, estado: str | None = None) -> list[dict[str, Any]]:
    """Registros del vector store reconstruidos desde SQLite (texto con prefijo + metadatos)."""
    docs = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))
    if not docs:
        return []
    d = docs[0]
    temas = temas_por_chunk(doc_id)
    areas = areas_mod.de_json(d.get("areas_aplica"))
    out = []
    for c in db.query("SELECT * FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,)):
        etiqueta = f"Art. {c['articulo']}" if str(c["articulo"]).isdigit() else c["articulo"]
        rec = {
            "id": c["chunk_id"],
            "text": f"[{d['nombre_norma']} v{d['version']} · {etiqueta}] {c['texto']}",
            "doc_id": doc_id, "codigo": d.get("codigo") or doc_id, "nombre_norma": d["nombre_norma"],
            "entidad": d["entidad"], "tipo": d.get("tipo_fuente") or d.get("tipo"), "tema": d["tema"],
            "articulo": str(c["articulo"]), "pagina": c["pagina"],
            "fecha_emision": fecha_int(d["fecha_emision"]), "fecha_vigencia": fecha_int(d["fecha_vigencia"]),
            "estado": estado or d["estado"], "version": d["version"], "reemplaza_a": d["reemplaza_a"],
        }
        if temas.get(c["chunk_id"]):
            rec["temas"] = temas[c["chunk_id"]]
        if areas:
            rec["areas_aplica"] = areas
        out.append(rec)
    return out


def reindexar(doc_id: str, estado: str | None = None) -> int:
    recs = records_de(doc_id, estado)
    return get_store().upsert(recs) if recs else 0


# ---------------------------------------------------------------------------- vigencia y reemplazo
def codigos_derogados(texto: str, propio: str | None) -> list[str]:
    """Códigos citados junto a una cláusula de derogación (deróguese / déjese sin efecto / queda sin efecto)."""
    out: list[str] = []
    for m in DEROGA_RE.finditer(texto or ""):
        ini = max(0, texto.rfind("\n\n", 0, m.start()), m.start() - 200)
        fin_par = texto.find("\n\n", m.end())
        fin = min(m.end() + 250, fin_par if fin_par != -1 else len(texto))
        for c in CODIGO_RE.findall(texto[ini:fin]):
            if c != propio and c not in out:
                out.append(c)
    return out


def objetivos_reemplazo(d: dict[str, Any]) -> list[str]:
    md = json.loads(d.get("metadatos_json") or "{}")
    objs = [d["reemplaza_a"]] if d.get("reemplaza_a") else []
    objs += [c for c in md.get("deroga", []) if c not in objs]
    return objs


def reemplazante_publicado(d: dict[str, Any]) -> dict[str, Any] | None:
    """Documento ya publicado que declara reemplazar/derogar a `d` (caso: la antigua se carga después)."""
    codigo = d.get("codigo") or d["doc_id"]
    for y in db.query("SELECT * FROM documentos WHERE publicado = 1 AND doc_id != ? AND estado != 'rechazada'",
                      (d["doc_id"],)):
        if codigo in objetivos_reemplazo(y) and fecha_ref(y) >= fecha_ref(d):
            return y
    return None


def aplicar_reemplazos(doc_id: str, usuario: str | None) -> list[str]:
    """Marca como reemplazadas las normas que `doc_id` reemplaza/deroga (si ya están cargadas y son anteriores)."""
    d = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))[0]
    hechos = []
    for cod in objetivos_reemplazo(d):
        t = doc_por_codigo(cod)
        if not t:
            db.traza_ingesta(doc_id, "reemplazo", {"objetivo": cod},
                             {"aplicado": False, "motivo": "la norma reemplazada aún no está cargada; se marcará "
                                                           "al cargarla"}, usuario=usuario)
            continue
        if t["doc_id"] == doc_id:
            continue
        if fecha_ref(t) > fecha_ref(d):
            adv = (f"{t['doc_id']} tiene fecha posterior ({t['fecha_vigencia'] or t['fecha_emision']}) a {doc_id}: "
                   "no se aplica el reemplazo")
            db.traza_ingesta(doc_id, "reemplazo", {"objetivo": t["doc_id"]}, {"aplicado": False, "advertencia": adv},
                             usuario=usuario)
            md = json.loads(d.get("metadatos_json") or "{}")
            if adv not in md.setdefault("advertencias", []):
                md["advertencias"].append(adv)
                _set(doc_id, metadatos_json=json.dumps(md, ensure_ascii=False), requiere_confirmacion=1)
            continue
        if t["estado"] == "reemplazada" and t.get("reemplazada_por") == doc_id:
            continue
        nuevo = "reemplazada"  # el contrato usa 'reemplazada' también para la derogación explícita
        _set(t["doc_id"], estado=nuevo, reemplazada_por=doc_id, estado_actualizado_at=db.now())
        n = reindexar(t["doc_id"])
        hechos.append(t["doc_id"])
        # sus conflictos con otras normas dejan de estar abiertos (la versión vigente es la nueva)
        db.execute("UPDATE discrepancias SET estado = 'resuelta', comentario = COALESCE(comentario, ?) "
                   "WHERE (doc_a = ? OR doc_b = ?) AND COALESCE(estado, 'abierta') = 'abierta' AND tipo != 'actualiza'",
                   (f"{t['doc_id']} reemplazada por {doc_id}", t["doc_id"], t["doc_id"]))
        db.traza_ingesta(doc_id, "reemplazo", {"objetivo": t["doc_id"]},
                         {"aplicado": True, "estado": nuevo, "chunks_reindexados": n}, usuario=usuario)
        log.info("%s reemplaza a %s (marcada %s, %d chunks reindexados)", doc_id, t["doc_id"], nuevo, n)
    return hechos


# ---------------------------------------------------------------------------- pipeline
def recibir(path: str | Path, metadatos: dict[str, Any] | None = None, usuario: str = "sistema",
            nombre_original: str | None = None) -> dict[str, Any]:
    """Paso síncrono y rápido: rechazo de archivos prohibidos, hash, duplicados, parseo, frontmatter y doc_id.

    Devuelve un contexto para procesar(). Si es duplicado devuelve {duplicado: True, ...} y no hace nada más.
    """
    path = Path(path)
    nombre = nombre_original or path.name
    if es_archivo_prohibido(nombre):
        raise ArchivoRechazado(f"'{nombre}' es material interno del equipo (00_GUIA / NO_SUBIR): no se indexa")
    sha = sha256_de(path)
    previo = doc_por_sha(sha)
    reintento = previo if previo and previo["estado_ingesta"] == "error" else None
    if previo and not reintento:
        db.traza_ingesta(previo["doc_id"], "recepcion", {"archivo": nombre, "sha256": sha},
                         {"duplicado": True, "doc_id": previo["doc_id"]}, usuario=usuario)
        return {"doc_id": previo["doc_id"], "codigo": previo.get("codigo"), "duplicado": True,
                "estado_ingesta": previo["estado_ingesta"],
                "mensaje": f"El archivo ya estaba cargado como {previo['doc_id']} (mismo sha256): no se reindexa."}

    paginas, basura, motivo = parsear(path)
    texto_total = "\n".join(t for _, t in paginas)
    fm, _ = separar_frontmatter(texto_total)
    fm_origen = "frontmatter" if FM_RE.match(texto_total or "") else ("ficha" if fm else None)
    if fm and paginas:  # el frontmatter no es contenido normativo
        _, cuerpo1 = separar_frontmatter(paginas[0][1])
        paginas = [(paginas[0][0], cuerpo1)] + paginas[1:]
    if metadatos is None:
        metadatos = metadatos_corpus(nombre)
    codigo = None
    if metadatos:
        codigo = metadatos.get("codigo") or metadatos.get("doc_id")
    elif fm:
        codigo = _desde_frontmatter(fm).get("codigo")
    version = (metadatos or {}).get("version") or _desde_frontmatter(fm).get("version") if (metadatos or fm) else None
    doc_id = reintento["doc_id"] if reintento else codigo or f"DOC-{sha[:8].upper()}"
    if reintento:  # el intento anterior terminó en error: se reprocesa sobre la misma fila (nada se borra)
        db.execute("UPDATE documentos SET estado_ingesta = 'procesando', etapa = 'recibido', mensaje_ingesta = NULL "
                   "WHERE doc_id = ?", (doc_id,))
    elif db.query("SELECT 1 FROM documentos WHERE doc_id = ?", (doc_id,)):
        alt = f"{doc_id}_v{version}" if version else None
        if alt and not db.query("SELECT 1 FROM documentos WHERE doc_id = ?", (alt,)):
            doc_id = alt
        else:
            doc_id = f"{doc_id}-{sha[:6].upper()}"  # mismo código y versión, contenido distinto

    with db.connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO documentos (doc_id, codigo, nombre_norma, archivo, sha256, n_chunks, estado_ingesta, "
            "estado, publicado, confirmado, etapa, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, codigo, nombre, str(path), sha, 0, "procesando", None, 0, 0, "recibido", db.now()))
    db.traza_ingesta(doc_id, "recepcion", {"archivo": nombre, "sha256": sha, "bytes": path.stat().st_size},
                     {"doc_id": doc_id, "frontmatter": bool(fm), "paginas": len(paginas), "reintento": bool(reintento),
                      "texto_insuficiente": basura, "motivo": motivo or None}, usuario=usuario)
    return {"doc_id": doc_id, "codigo": codigo, "duplicado": False, "estado_ingesta": "procesando",
            "path": str(path), "nombre": nombre, "sha": sha, "paginas": paginas, "frontmatter": fm,
            "metadatos": metadatos, "basura": basura, "motivo": motivo, "usuario": usuario,
            "fm_origen": fm_origen}


def procesar(ctx: dict[str, Any], validar: bool = True, canal: str = "web") -> dict[str, Any]:
    """Pasos lentos: metadatos (LLM si hace falta), chunks, afirmaciones, comparación y publicación."""
    doc_id, usuario = ctx["doc_id"], ctx.get("usuario")
    try:
        return _procesar(ctx, validar, canal)
    except Exception as e:
        log.exception("Error procesando %s", doc_id)
        _set(doc_id, estado_ingesta="error", etapa="error", mensaje_ingesta=f"Error: {e}", publicado=0,
             estado="pendiente_revision")
        db.traza_ingesta(doc_id, "error", None, {"error": str(e)}, usuario=usuario)
        try:  # lo que alcanzó a indexarse no debe usarse
            reindexar(doc_id, "pendiente_revision")
        except Exception:
            log.exception("No se pudo marcar los chunks de %s como pendiente_revision", doc_id)
        return resumen(doc_id)


def _procesar(ctx: dict[str, Any], validar: bool, canal: str) -> dict[str, Any]:
    doc_id, usuario, paginas = ctx["doc_id"], ctx.get("usuario"), ctx["paginas"]
    texto = "\n".join(t for _, t in paginas)

    # ---- metadatos
    etapa(doc_id, "extrayendo_metadatos")
    t0 = time.perf_counter()
    r = resolver_metadatos(ctx.get("frontmatter") or {}, ctx.get("metadatos"), texto, ctx["sha"], doc_id, usuario)
    meta = r["meta"]
    deroga = codigos_derogados(texto, meta["codigo"])
    if not meta["reemplaza_a"] and deroga:
        meta["reemplaza_a"] = deroga[0]
        r["fuente"]["reemplaza_a"] = "clausula_derogacion"
        r["confianza"]["reemplaza_a"] = 1.0
    tipo_fuente = tipo_fuente_de(meta["entidad"])
    if ctx.get("areas_usuario"):  # elegidas por el usuario al cargar: prevalecen, sin consultar al LLM
        ar = {"areas_aplica": ctx["areas_usuario"], "areas_fuente": "manual", "areas_confianza": 1.0,
              "areas_requiere_confirmacion": 0, "declarado": ctx["areas_usuario"], "descartadas": [], "llm": None}
    else:
        ar = resolver_areas(ctx.get("frontmatter") or {}, ctx.get("fm_origen"), texto,
                            meta["nombre"] or ctx.get("nombre"), doc_id, usuario)
    md = {"campos": meta, "fuente": r["fuente"], "origen": r["origen"], "faltantes": r["faltantes"],
          "baja_confianza": r["baja_confianza"], "advertencias": r["advertencias"], "deroga": deroga,
          "archivo": ctx.get("nombre")}
    _set(doc_id, codigo=meta["codigo"] or ctx.get("codigo"), nombre_norma=meta["nombre"] or ctx.get("nombre"),
         entidad=meta["entidad"], tipo=tipo_fuente, tipo_fuente=tipo_fuente, fecha_emision=meta["fecha_emision"],
         fecha_vigencia=meta["fecha_vigencia"], version=meta["version"] or "1", reemplaza_a=meta["reemplaza_a"],
         metadatos_json=json.dumps(md, ensure_ascii=False), confianza_json=json.dumps(r["confianza"]),
         requiere_confirmacion=r["requiere_confirmacion"], estado_inferido=r["estado_inferido"],
         areas_aplica=json.dumps(ar["areas_aplica"], ensure_ascii=False), areas_fuente=ar["areas_fuente"],
         areas_confianza=ar["areas_confianza"], areas_requiere_confirmacion=ar["areas_requiere_confirmacion"],
         confirmado=int(r["origen"] in ("frontmatter", "provisto") and not r["requiere_confirmacion"]))
    llm = r.get("llm") or {}
    db.traza_ingesta(doc_id, "metadatos", {"origen": r["origen"]},
                     {"metadatos": meta, "confianza": r["confianza"], "fuente": r["fuente"], "tipo": tipo_fuente,
                      "requiere_confirmacion": bool(r["requiere_confirmacion"]), "faltantes": r["faltantes"],
                      "estado_inferido": bool(r["estado_inferido"]), "advertencias": r["advertencias"],
                      "deroga": deroga, "areas": ar},
                     llm.get("modelo") if llm else None, llm.get("tokens") if llm else None,
                     llm.get("latencia_ms") if llm else int((time.perf_counter() - t0) * 1000), usuario)

    # ---- chunks
    d = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))[0]
    chunks = chunkear(paginas, {"doc_id": doc_id, "codigo": d["codigo"] or doc_id, "nombre_norma": d["nombre_norma"],
                                "version": d["version"]}) if texto.strip() else []
    # si otro documento ya usa el mismo código+versión, los ids de vector usan el doc_id (evita pisarlo)
    if chunks and db.query("SELECT 1 FROM chunks WHERE chunk_id = ? AND doc_id != ?", (chunks[0]["chunk_id"], doc_id)):
        chunks = chunkear(paginas, {"doc_id": doc_id, "codigo": doc_id, "nombre_norma": d["nombre_norma"],
                                    "version": d["version"]})
    cuerpo = re.sub(r"\s", "", texto)
    if ctx.get("basura") or not chunks or len(cuerpo) < MIN_CHARS:
        motivo = ctx.get("motivo") or ("No se detectaron artículos ni disposiciones finales" if not chunks
                                       else f"Texto insuficiente ({len(cuerpo)} caracteres)")
        msg = (f"El documento requiere revisión y NO se indexó: {motivo}. Verifique que el archivo tenga texto "
               "seleccionable (no escaneado) y artículos numerados ('Artículo N').")
        _set(doc_id, estado_ingesta="requiere_revision", etapa="requiere_revision", mensaje_ingesta=msg,
             estado="pendiente_revision", publicado=0, n_chunks=0)
        db.traza_ingesta(doc_id, "chunks", {"caracteres": len(cuerpo)},
                         {"n_chunks": len(chunks), "indexado": False, "motivo": motivo}, usuario=usuario)
        return resumen(doc_id)
    with db.connect() as con:
        for c in chunks:
            con.execute("INSERT OR REPLACE INTO chunks (chunk_id, doc_id, articulo, seccion, pagina, texto) "
                        "VALUES (?,?,?,?,?,?)", (c["chunk_id"], doc_id, c["articulo"], c["seccion"], c["pagina"],
                                                c["texto"]))
            con.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (c["chunk_id"],))
            con.execute("INSERT INTO chunks_fts (chunk_id, doc_id, articulo, texto) VALUES (?,?,?,?)",
                        (c["chunk_id"], doc_id, c["articulo"], c["texto"]))
        con.execute("UPDATE documentos SET n_chunks = ? WHERE doc_id = ?", (len(chunks), doc_id))
    db.traza_ingesta(doc_id, "chunks", {"caracteres": len(cuerpo)},
                     {"n_chunks": len(chunks), "chunks": [{"chunk_id": c["chunk_id"], "articulo": c["articulo"],
                                                           "pagina": c["pagina"]} for c in chunks]},
                     usuario=usuario)

    # ---- estado base (vigencia / reemplazo cuando la antigua llega después)
    d = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))[0]
    estado_base = meta["estado"]
    posterior = reemplazante_publicado(d)
    if posterior:
        estado_base = "reemplazada"
        _set(doc_id, reemplazada_por=posterior["doc_id"], estado_actualizado_at=db.now())
        db.traza_ingesta(doc_id, "reemplazo", {"reemplazante": posterior["doc_id"]},
                         {"aplicado": True, "estado": "reemplazada",
                          "motivo": f"{posterior['doc_id']} (ya cargada) declara reemplazar a esta norma"},
                         usuario=usuario)

    with LOCK:
        # ---- afirmaciones + índice (estado provisional 'procesando': el Agente 2 no lo usa aún)
        etapa(doc_id, "indexando")
        _set(doc_id, estado="procesando")
        from app.normas import validacion
        afirmaciones = validacion.extraer_afirmaciones(doc_id, usuario) if validar else []
        n = reindexar(doc_id, "procesando")
        db.traza_ingesta(doc_id, "indexacion", None, {"vectores_upsert": n, "backend": get_store().backend,
                                                      "estado_provisional": "procesando"}, usuario=usuario)

        # ---- comparación
        bloqueantes = 0
        if validar and afirmaciones and estado_base in ("vigente", "por_entrar_en_vigencia"):
            etapa(doc_id, "comparando")
            res = validacion.validar_documento(doc_id, usuario=usuario, canal=canal)
            bloqueantes = res.get("altas_bloqueantes", 0)
        elif validar:
            db.traza_ingesta(doc_id, "comparacion", None,
                             {"omitida": True, "motivo": "sin afirmaciones cuantitativas" if not afirmaciones
                              else f"la norma está {estado_base}: no se compara contra el corpus vigente"},
                             usuario=usuario)

        # ---- publicación
        etapa(doc_id, "informe")
        if bloqueantes:
            _set(doc_id, estado="pendiente_revision", publicado=0, estado_ingesta="indexado",
                 estado_actualizado_at=db.now())
            reindexar(doc_id, "pendiente_revision")
            msg = (f"{bloqueantes} discrepancia(s) ALTA: la norma queda pendiente de revisión humana y no se "
                   "publica hasta su decisión.")
            db.traza_ingesta(doc_id, "publicacion", {"altas_bloqueantes": bloqueantes},
                             {"publicado": False, "estado": "pendiente_revision", "mensaje": msg}, usuario=usuario)
        else:
            _set(doc_id, estado=estado_base, publicado=1, estado_ingesta="indexado")
            reindexar(doc_id)
            hechos = aplicar_reemplazos(doc_id, usuario)
            msg = "Publicada automáticamente (sin discrepancias ALTA)." + (
                f" Marcó como reemplazada(s): {', '.join(hechos)}." if hechos else "")
            db.traza_ingesta(doc_id, "publicacion", {"altas_bloqueantes": 0},
                             {"publicado": True, "estado": estado_base, "reemplaza": hechos, "mensaje": msg},
                             usuario=usuario)
        etapa(doc_id, "listo", msg)
    log.info("Ingerido %s (%d chunks, %d afirmaciones): %s", doc_id, len(chunks), len(afirmaciones), msg)
    return resumen(doc_id)


def resumen(doc_id: str) -> dict[str, Any]:
    d = db.query("SELECT doc_id, codigo, estado, publicado, estado_ingesta, etapa, mensaje_ingesta, n_chunks "
                 "FROM documentos WHERE doc_id = ?", (doc_id,))
    if not d:
        return {"doc_id": doc_id, "estado_ingesta": "error", "mensaje": "documento no encontrado"}
    d = d[0]
    n_altas = db.query("SELECT COUNT(*) n FROM discrepancias WHERE (doc_a = ? OR doc_b = ?) AND severidad = 'alta' "
                       "AND COALESCE(estado,'abierta') = 'abierta'", (doc_id, doc_id))[0]["n"]
    return {"doc_id": doc_id, "codigo": d["codigo"], "estado_ingesta": d["estado_ingesta"], "etapa": d["etapa"],
            "estado": d["estado"], "publicado": d["publicado"], "n_chunks": d["n_chunks"],
            "n_discrepancias_altas": n_altas, "mensaje": d["mensaje_ingesta"], "duplicado": False}


def ingerir(path: str | Path, metadatos: dict[str, Any] | None = None, validar: bool = True,
            esperar: bool = True, usuario: str = "sistema", canal: str = "web") -> dict[str, Any]:
    """Ingesta completa y síncrona. Devuelve {doc_id, codigo, estado_ingesta, estado, publicado, duplicado, ...}."""
    ctx = recibir(path, metadatos, usuario)
    if ctx.get("duplicado"):
        return ctx
    return procesar(ctx, validar=validar, canal=canal)


# ---------------------------------------------------------------------------- control humano
DECISIONES = ("aprobar_publicar", "publicar_igual", "rechazar")


def decidir(doc_id: str, decision: str, usuario: str | None = None, comentario: str | None = None) -> dict[str, Any]:
    if decision not in DECISIONES:
        raise ValueError(f"decision debe ser una de {DECISIONES}")
    rows = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))
    if not rows:
        raise KeyError(doc_id)
    d = rows[0]
    with LOCK:
        if decision == "rechazar":
            _set(doc_id, estado="rechazada", publicado=0, estado_actualizado_at=db.now())
            reindexar(doc_id, "rechazada")
            # las discrepancias de una norma rechazada ya no afectan al corpus publicado
            db.execute("UPDATE discrepancias SET estado = 'resuelta', comentario = COALESCE(comentario, ?) "
                       "WHERE doc_a = ? OR doc_b = ?", (f"norma {doc_id} rechazada", doc_id, doc_id))
            hechos: list[str] = []
        else:
            md = json.loads(d.get("metadatos_json") or "{}")
            estado = (md.get("campos") or {}).get("estado") or "vigente"
            if estado in ("pendiente_revision", "procesando", "rechazada"):
                estado = "vigente"
            if reemplazante_publicado(d):
                estado = "reemplazada"
            _set(doc_id, estado=estado, publicado=1, estado_actualizado_at=db.now(), confirmado=1)
            reindexar(doc_id)
            hechos = aplicar_reemplazos(doc_id, usuario)
            if decision == "aprobar_publicar":  # el revisor da por atendidas las discrepancias
                db.execute("UPDATE discrepancias SET estado = 'resuelta' WHERE doc_a = ? AND "
                           "COALESCE(estado,'abierta') = 'abierta'", (doc_id,))
        nuevo = db.query("SELECT estado, publicado FROM documentos WHERE doc_id = ?", (doc_id,))[0]
        db.traza_ingesta(doc_id, "decision_usuario", {"decision": decision, "comentario": comentario},
                         {"estado": nuevo["estado"], "publicado": bool(nuevo["publicado"]), "reemplaza": hechos},
                         usuario=usuario)
        etapa(doc_id, "listo", f"Decisión del usuario: {decision}")
    return {"ok": True, "estado": nuevo["estado"], "publicado": nuevo["publicado"]}
