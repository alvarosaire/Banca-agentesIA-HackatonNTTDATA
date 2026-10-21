"""Agente 1 — validación: afirmaciones, candidatos, comparación, severidad y citas verificables.

Sin comparar todo contra todo:
  a) extraer_afirmaciones(doc): UNA llamada al LLM por documento -> afirmaciones cuantitativas
     [{tema, valor, unidad, condicion, articulo, cita_literal, sentido}] con temas normalizados (TEMAS_AF).
     Se guardan en la tabla `afirmaciones` y alimentan la lista `temas` de cada chunk en el índice.
  b) candidatos: por afirmación, búsqueda en el índice con filtro {temas: $in [tema], estado: vigente,
     doc_id: $ne doc}; se complementa con la tabla `afirmaciones` por tema (cubre la consistencia eventual
     de Pinecone). SQLite es la fuente de verdad del estado (vigente y publicado).
  c) comparación en código: normaliza unidades (horas<->días calendario, años<->meses, % vs fracción,
     montos S/). Días hábiles vs calendario no se convierten: par 'incomparable' que solo se registra si
     el LLM lo confirma. Valores iguales -> no hay conflicto. Valores distintos -> el LLM confirma leyendo
     ambos textos literales (en lotes) y devuelve {conflicto, cita_a, cita_b, explicacion}.
  d) severidad y prevalencia (código, no LLM): externa > interna; entre iguales prevalece la más reciente.
     ALTA: interna menos estricta (o distinta) que una externa vigente. MEDIA: interna vs interna, o
     externa vs externa (prevalece la reciente). INFORMATIVA: interna más estricta, o contra la versión
     anterior que la norma reemplaza.
  Citas: cita_a / cita_b deben existir literalmente (normalizando espacios, mayúsculas y **) en el chunk
  fuente; si no, se prueba la cita de la afirmación; si tampoco, se descarta y se registra el motivo.
Todo queda en trazas_ingesta; además se guarda un Run (agente 'validador') en `trazas` para los KPIs.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agents.trace import store as run_store
from app.config import get_settings
from app.normas import db
from app.normas.vector import get_store

log = logging.getLogger("validacion")

TEMAS_AF = ["cuota_ingreso", "refinanciacion_plazo", "reporte_central_riesgos", "conservacion_expedientes",
            "provision_por_categoria", "clasificacion_dias_atraso", "antiguedad_minima", "plazo_reporte_ros",
            "facultad_aprobacion", "relacion_prestamo_valor", "plazo_maximo_credito", "otro"]
CATEGORIAS = {"normal": r"\bnormal\b", "cpp": r"\bcpp\b|problemas\s+potenciales", "deficiente": r"deficiente",
              "dudoso": r"dudos", "perdida": r"p[ée]rdida"}
# Sentido de cada tema: 'maximo' = el valor es un tope (menor = más estricto);
# 'minimo' = el valor es una exigencia mínima (mayor = más estricto). Lo fija el código, no el LLM.
SENTIDO = {"cuota_ingreso": "maximo", "refinanciacion_plazo": "maximo", "reporte_central_riesgos": "maximo",
           "conservacion_expedientes": "minimo", "provision": "minimo", "clasificacion": "maximo",
           "antiguedad_minima": "minimo", "plazo_reporte_ros": "maximo", "facultad_aprobacion": "maximo",
           "relacion_prestamo_valor": "maximo", "plazo_maximo_credito": "maximo"}
TAM_LOTE = 6
MAX_AFIRMACIONES = 120


# ---------------------------------------------------------------------------- normalización
def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c))


def norm_cita(t: str) -> str:
    t = (t or "").replace("**", "").replace("__", "")
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace(" ", " ")
    return " ".join(t.split()).strip(" .;:,\"'").lower()


def cita_verificada(cita: str | None, texto: str) -> bool:
    c = norm_cita(cita or "")
    return len(c) >= 8 and c in norm_cita(texto)


def _categoria(*textos: str) -> str | None:
    t = _sin_tildes(" ".join(x or "" for x in textos)).lower()
    for cat, pat in CATEGORIAS.items():
        if re.search(_sin_tildes(pat), t):
            return cat
    return None


def norm_tema(tema: str | None, condicion: str | None, cita: str | None) -> str:
    t = re.sub(r"[^a-z0-9_]+", "_", _sin_tildes(tema or "").lower()).strip("_")
    for base in ("provision", "clasificacion"):
        if t.startswith(base):
            cat = _categoria(t.replace(base, "")) or _categoria(condicion or "") or _categoria(cita or "")
            return f"{base}_{cat}" if cat else base + "_por_categoria"
    return t if t in TEMAS_AF else "otro"


def tema_base(tema: str) -> str:
    for base in ("provision", "clasificacion"):
        if tema.startswith(base):
            return base
    return tema


def norm_unidad(u: str | None) -> str:
    u = _sin_tildes(u or "").lower()
    if "%" in u or "porc" in u or "pct" in u:
        return "pct"
    if "fracc" in u or "tanto por uno" in u:
        return "fraccion"
    if "habil" in u:
        return "dias_habiles"
    if "hora" in u:
        return "horas"
    if "dia" in u:
        return "dias_calendario"
    if "mes" in u:
        return "meses"
    if "ano" in u or "anio" in u:
        return "anios"
    if "s/" in u or "sol" in u or "pen" in u or "monto" in u:
        return "soles"
    if "vez" in u or "vece" in u:
        return "veces"
    return u.strip() or "otro"


def parse_num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v or "")
    m = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?!\d)|\d+(?:[.,]\d+)?", s)
    if not m:
        return None
    x = m.group(0)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", x):
        x = x.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", x):
        x = x.replace(".", "")
    else:
        x = x.replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return None


def canonico(valor: float, unidad: str) -> tuple[str, float]:
    """-> (dimensión, valor en la unidad base de esa dimensión)."""
    if unidad == "fraccion":
        return "pct", valor * 100 if valor <= 1 else valor
    if unidad == "horas":
        return "tiempo_h", valor
    if unidad == "dias_calendario":
        return "tiempo_h", valor * 24
    if unidad == "anios":
        return "tiempo_m", valor * 12
    if unidad == "meses":
        return "tiempo_m", valor
    return unidad, valor


def mas_estricto(v1: float, v2: float, sentido: str | None) -> bool | None:
    """¿v1 es más estricto que v2? None si no se puede saber."""
    if sentido == "maximo":
        return v1 < v2
    if sentido == "minimo":
        return v1 > v2
    return None


# ---------------------------------------------------------------------------- a) afirmaciones
PROMPT_AF = """Eres el analista normativo de un banco. Extrae las AFIRMACIONES CUANTITATIVAS (reglas con un número:
porcentaje, monto, plazo, días, horas, años, cantidad de veces) de cada artículo de esta norma.

Norma: {nombre} ({codigo}), emitida por {entidad}.

Temas permitidos (usa exactamente uno por afirmación):
- cuota_ingreso: relación cuota/ingreso máxima del cliente
- refinanciacion_plazo: plazo para solicitar refinanciación tras el incumplimiento
- reporte_central_riesgos: plazo para enviar el reporte a la central de riesgos
- conservacion_expedientes: tiempo de conservación de expedientes de crédito
- provision_<categoria>: % de provisión por categoría (provision_normal, provision_cpp, provision_deficiente,
  provision_dudoso, provision_perdida) — una afirmación por categoría
- clasificacion_<categoria>: días de atraso máximos de la categoría (clasificacion_normal, clasificacion_cpp, ...);
  para un rango "de 16 a 30 días" usa el límite superior (30)
- antiguedad_minima: antigüedad laboral o del negocio exigida
- plazo_reporte_ros: plazo para reportar una operación sospechosa a la unidad de inteligencia financiera
- facultad_aprobacion: monto máximo que puede aprobar una instancia (agencia, zonal...); indica la instancia en condicion
- relacion_prestamo_valor: % máximo financiado sobre el valor de tasación
- plazo_maximo_credito: plazo máximo del crédito
- otro: cualquier otra regla cuantitativa

Campos de cada afirmación:
- chunk_id: el id del fragmento (entre corchetes) donde está la regla
- articulo: número de artículo (o "Disposición final")
- tema: de la lista
- valor: SOLO el número (ej. 40, 0.7, 20000, 10). "décimo día hábil" -> 10; "una vez" -> 1
- unidad: una de "%", "dias_calendario", "dias_habiles", "horas", "meses", "anios", "soles", "veces", "otro"
  (si dice "días" sin precisar, usa "dias_calendario")
- condicion: a quién/qué aplica y bajo qué condición, breve (ej. "general", "categoría Deficiente",
  "instancia: agencia", "excepción con aprobación del Comité de Riesgos")
- sentido: "maximo" si el valor es un tope, "minimo" si es una exigencia mínima
- cita_literal: la frase EXACTA del fragmento que contiene el valor, copiada carácter por carácter

No inventes reglas: si un artículo no tiene números, no generes afirmaciones para él.
También indica el tema_principal del documento, uno de: {temas_doc}.

Responde SOLO JSON: {{"tema_principal": "...", "afirmaciones": [{{...}}]}}

Fragmentos:
{fragmentos}"""


def extraer_afirmaciones(doc_id: str, usuario: str | None = None) -> list[dict[str, Any]]:
    from app.normas.ingesta import llm_json

    d = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))[0]
    chunks = db.query("SELECT chunk_id, articulo, texto FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,))
    if not chunks:
        return []
    frag = "\n\n".join(f"[{c['chunk_id']}] (Art. {c['articulo']})\n{c['texto']}" for c in chunks)
    prompt = PROMPT_AF.format(nombre=d["nombre_norma"], codigo=d.get("codigo") or doc_id, entidad=d["entidad"],
                              temas_doc=", ".join(db.TEMAS), fragmentos=frag[:24000])
    data, info = llm_json(prompt, doc_id, "afirmaciones", usuario, {"n_chunks": len(chunks)})
    por_id = {c["chunk_id"]: c for c in chunks}
    por_art = {}
    for c in chunks:
        por_art.setdefault(str(c["articulo"]).lower(), c)
    lista = data.get("afirmaciones") if isinstance(data, dict) else data if isinstance(data, list) else []
    out, descartadas = [], []
    for a in (lista or [])[:MAX_AFIRMACIONES]:
        if not isinstance(a, dict):
            continue
        ch = por_id.get(str(a.get("chunk_id") or "").strip())
        if not ch:
            art = re.sub(r"(?i)^(art[ií]culo|art\.?)\s*", "", str(a.get("articulo") or "")).strip(" .").lower()
            ch = por_art.get(art)
        valor = parse_num(a.get("valor"))
        if not ch or valor is None:
            descartadas.append({"afirmacion": a, "motivo": "sin fragmento" if not ch else "valor no numérico"})
            continue
        tema = norm_tema(a.get("tema"), a.get("condicion"), a.get("cita_literal"))
        unidad = norm_unidad(a.get("unidad"))
        sentido = SENTIDO.get(tema_base(tema)) or (a.get("sentido") if a.get("sentido") in ("maximo", "minimo")
                                                   else None)
        cita = str(a.get("cita_literal") or "").strip()
        out.append({"doc_id": doc_id, "chunk_id": ch["chunk_id"], "articulo": str(ch["articulo"]), "tema": tema,
                    "valor": valor, "unidad": unidad, "condicion": str(a.get("condicion") or "").strip()[:300],
                    "sentido": sentido, "cita_literal": cita[:600],
                    "cita_ok": cita_verificada(cita, ch["texto"])})
    with db.connect() as con:
        con.execute("DELETE FROM afirmaciones WHERE doc_id = ?", (doc_id,))
        for a in out:
            a["id"] = con.execute(
                "INSERT INTO afirmaciones (doc_id, chunk_id, articulo, tema, valor, unidad, condicion, sentido,"
                " cita_literal, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (doc_id, a["chunk_id"], a["articulo"], a["tema"], a["valor"], a["unidad"], a["condicion"],
                 a["sentido"], a["cita_literal"], db.now())).lastrowid
        tp = data.get("tema_principal") if isinstance(data, dict) else None
        con.execute("UPDATE documentos SET tema = ? WHERE doc_id = ?", (tp if tp in db.TEMAS else "otros", doc_id))
    db.traza_ingesta(doc_id, "afirmaciones", {"n_chunks": len(chunks)},
                     {"tema_principal": tp, "n": len(out), "afirmaciones": [
                         {k: a[k] for k in ("chunk_id", "articulo", "tema", "valor", "unidad", "condicion",
                                            "sentido", "cita_literal", "cita_ok")} for a in out],
                      "descartadas": descartadas, "error_llm": info.get("error")},
                     info.get("modelo"), info.get("tokens"), info.get("latencia_ms"), usuario)
    return out


# ---------------------------------------------------------------------------- b) candidatos
def _docs() -> dict[str, dict[str, Any]]:
    return {d["doc_id"]: d for d in db.query("SELECT * FROM documentos")}


def buscar_candidatos(doc_id: str, afs: list[dict[str, Any]]) -> tuple[list[tuple[dict, dict, str, float | None]],
                                                                        list[dict], list[dict]]:
    """-> (pares, traza_por_afirmacion, hits_para_kpi). Cada par = (afirmación nueva, afirmación candidata,
    fuente 'pinecone'|'sqlite_afirmaciones', score)."""
    store = get_store()
    pares, traza, hits_kpi, vistos = [], [], [], set()
    for af in afs:
        q = f"{af['tema'].replace('_', ' ')} {af['condicion']} {af['cita_literal']}"
        flt = {"temas": {"$in": [af["tema"]]}, "estado": "vigente", "doc_id": {"$ne": doc_id}}
        pine: dict[str, float] = {}
        err = None
        try:
            for h in store.search(q, top_k=10, filter=flt):
                pine[h.id] = round(h.best_score, 4)
                hits_kpi.append({"id": h.id, "score": round(h.best_score, 4), "doc_id": h.metadata.get("doc_id"),
                                 "tema": af["tema"]})
        except Exception as e:  # el índice no responde: queda el complemento por SQLite
            err = str(e)
            log.warning("Búsqueda de candidatos falló (%s): %s", af["tema"], e)
        sql = db.query(
            "SELECT a.* FROM afirmaciones a JOIN documentos d ON d.doc_id = a.doc_id WHERE a.tema = ? "
            "AND a.doc_id != ? AND d.estado = 'vigente' AND d.publicado = 1 ORDER BY a.id", (af["tema"], doc_id))
        if af["tema"] == "otro":  # tema abierto: solo los 3 fragmentos más parecidos según el índice
            top = sorted(pine, key=lambda k: -pine[k])[:3]
            sql = [s for s in sql if s["chunk_id"] in top]
        desfasados = [cid for cid in pine if not any(s["chunk_id"] == cid for s in sql)]
        cands = []
        for s in sql:
            fuente = "pinecone" if s["chunk_id"] in pine else "sqlite_afirmaciones"
            clave = (af["id"], s["id"])
            if clave in vistos:
                continue
            vistos.add(clave)
            pares.append((af, s, fuente, pine.get(s["chunk_id"])))
            cands.append({"doc_id": s["doc_id"], "chunk_id": s["chunk_id"], "valor": s["valor"], "unidad": s["unidad"],
                          "fuente": fuente, "score": pine.get(s["chunk_id"])})
        traza.append({"afirmacion_id": af["id"], "tema": af["tema"], "valor": af["valor"], "unidad": af["unidad"],
                      "consulta": q,
                      "n_pinecone": len(pine), "n_candidatos": len(cands), "candidatos": cands,
                      "pinecone_no_vigentes_o_desfasados": desfasados, "error_indice": err,
                      "nota": "candidatos = afirmaciones del mismo tema en normas vigentes y publicadas (SQLite); "
                              "fuente 'sqlite_afirmaciones' = el índice vectorial no trajo ese chunk"})
    return pares, traza, hits_kpi


# ---------------------------------------------------------------------------- c) comparación
PROMPT_CONF = """Eres el validador normativo de un banco. Para cada par de reglas decide si hay un CONFLICTO real:
ambas regulan exactamente la misma regla (mismo sujeto, producto, instancia, categoría y condición) y fijan
valores incompatibles. Una norma que cambia el valor fijado por otra SÍ es conflicto (luego se decide cuál prevalece).
Una regla GENERAL (para todo deudor minorista, toda la cartera o todo crédito) también rige para sus subproductos
(consumo, microempresa, pequeña empresa): si el subproducto fija otro valor para la misma regla, SÍ es conflicto.
conflicto=false si: regulan cosas distintas (p. ej. registros de operaciones LAFT vs expedientes de crédito;
crédito hipotecario vs consumo), una es una excepción condicionada de la otra (p. ej. "hasta 45% con aprobación
del Comité" frente a un 40% general), o en realidad dicen lo mismo.
Si el par indica "unidades_distintas" (p. ej. días hábiles vs calendario) marca conflicto=true solo si aun así
son claramente incompatibles.
cita_a / cita_b: copia LITERAL, carácter por carácter, de la frase del TEXTO A / TEXTO B que contiene el valor
(sin parafrasear, sin agregar ni quitar palabras, sin el número de artículo si no está en el texto).

Responde SOLO JSON: {{"resultados": [{{"par": <id>, "conflicto": true|false, "cita_a": "...", "cita_b": "...",
"explicacion": "una frase"}}]}}

Pares:
{pares}"""


def _confirmar_lote(lote: list[dict[str, Any]], doc_id: str, usuario: str | None) -> tuple[dict[int, dict], dict]:
    from app.normas.ingesta import llm_json

    bloques = []
    for p in lote:
        bloques.append(
            f"### par {p['par']} — tema {p['tema']}" + (" — unidades_distintas" if p["incomparable"] else "") + "\n"
            f"A: {p['nombre_a']} ({p['doc_a']}), Art. {p['art_a']} — condición: {p['cond_a']} — valor: "
            f"{p['valor_a']} {p['unidad_a']}\nTEXTO A: {p['texto_a']}\n"
            f"B: {p['nombre_b']} ({p['doc_b']}), Art. {p['art_b']} — condición: {p['cond_b']} — valor: "
            f"{p['valor_b']} {p['unidad_b']}\nTEXTO B: {p['texto_b']}")
    data, info = llm_json(PROMPT_CONF.format(pares="\n\n".join(bloques)), doc_id, "comparacion", usuario,
                          {"pares": [p["par"] for p in lote]})
    res = data.get("resultados") if isinstance(data, dict) else data if isinstance(data, list) else []
    out = {}
    for r in res or []:
        if isinstance(r, dict):
            try:
                out[int(r.get("par"))] = r
            except (TypeError, ValueError):
                continue
    return out, info


def _texto_chunk(chunk_id: str) -> str:
    r = db.query("SELECT texto FROM chunks WHERE chunk_id = ?", (chunk_id,))
    return r[0]["texto"] if r else ""


# ---------------------------------------------------------------------------- d) severidad
def clasificar(a: dict[str, Any], b: dict[str, Any], va: float, vb: float, sentido: str | None,
               reemplaza: bool) -> dict[str, Any]:
    """a = documento nuevo, b = documento existente. Jerarquía en código."""
    from app.normas.ingesta import fecha_ref

    if reemplaza:
        return {"severidad": "informativa", "tipo": "actualiza", "prevalece": a["doc_id"], "bloquea": False,
                "motivo": f"{a['doc_id']} reemplaza a {b['doc_id']}: diferencia contra la versión anterior "
                          f"(prevalece la nueva)."}
    ta, tb = a.get("tipo_fuente") or "interna", b.get("tipo_fuente") or "interna"
    if ta != tb:
        a_interna = ta == "interna"
        interna, externa = (a, b) if a_interna else (b, a)
        vi, ve = (va, vb) if a_interna else (vb, va)
        est = mas_estricto(vi, ve, sentido)
        if est:
            return {"severidad": "informativa", "tipo": "mas_estricta", "prevalece": interna["doc_id"],
                    "bloquea": False,
                    "motivo": f"La norma interna {interna['doc_id']} es más estricta que la externa "
                              f"{externa['doc_id']} ({vi:g} vs {ve:g}, el valor es un {sentido}): la cumple y va "
                              f"más allá; no es contradicción."}
        detalle = (f"es menos estricta ({vi:g} vs {ve:g}, el valor es un {sentido})" if est is False
                   else "fija un valor distinto")
        return {"severidad": "alta", "tipo": "contradice", "prevalece": externa["doc_id"], "bloquea": a_interna,
                "motivo": f"Jerarquía: la norma externa {externa['doc_id']} prevalece sobre la interna "
                          f"{interna['doc_id']}, que {detalle}."
                          + ("" if a_interna else f" La norma interna {interna['doc_id']} debe adecuarse.")}
    reciente, antigua = (a, b) if fecha_ref(a) >= fecha_ref(b) else (b, a)
    clase = "internas" if ta == "interna" else "externas"
    return {"severidad": "media", "tipo": "contradice", "prevalece": reciente["doc_id"], "bloquea": False,
            "motivo": f"Entre normas {clase} prevalece la más reciente: {reciente['doc_id']} (vigencia "
                      f"{reciente.get('fecha_vigencia') or reciente.get('fecha_emision')}) sobre {antigua['doc_id']} "
                      f"(vigencia {antigua.get('fecha_vigencia') or antigua.get('fecha_emision')})."}


# ---------------------------------------------------------------------------- pipeline
def validar_documento(doc_id: str, usuario: str | None = "sistema", canal: str = "web",
                      background: bool = False) -> dict[str, Any]:
    from app.normas.ingesta import objetivos_reemplazo

    t0 = time.perf_counter()
    docs = _docs()
    d = docs[doc_id]
    afs = db.query("SELECT * FROM afirmaciones WHERE doc_id = ? ORDER BY id", (doc_id,))
    run = run_store.create("validacion", f"Validar {doc_id} — {d['nombre_norma']} v{d['version']} "
                                         f"({len(afs)} afirmaciones)")
    run.meta = {"agente": "validador", "canal": canal, "usuario": usuario, "modelo": get_settings().llm_model}
    run.add_step("validador", "input", run.input, doc_id=doc_id)

    # b) candidatos
    pares, traza_c, hits_kpi = buscar_candidatos(doc_id, afs)
    db.traza_ingesta(doc_id, "candidatos", {"n_afirmaciones": len(afs)},
                     {"n_pares": len(pares), "por_afirmacion": traza_c,
                      "filtro": "temas $in [tema], estado = vigente, doc_id != doc (+ complemento SQLite)"},
                     latencia_ms=int((time.perf_counter() - t0) * 1000), usuario=usuario)
    run.add_step("validador", "tool_call", f"buscar candidatos para {len(afs)} afirmaciones",
                 tool="val_buscar_candidatos")
    run.add_step("validador", "tool_result", f"{len(hits_kpi)} fragmentos del índice, {len(pares)} pares",
                 tool="val_buscar_candidatos", result={"candidatos": hits_kpi})

    # c) comparación determinista
    reemplazados = set(objetivos_reemplazo(d))
    por_llm, comp_traza = [], []
    for i, (a, b, fuente, score) in enumerate(pares):
        da, unidad_a = canonico(a["valor"], a["unidad"])
        db_, unidad_b = canonico(b["valor"], b["unidad"])
        fila = {"par": i, "tema": a["tema"], "a": f"{a['doc_id']} art {a['articulo']}: {a['valor']:g} {a['unidad']}",
                "b": f"{b['doc_id']} art {b['articulo']}: {b['valor']:g} {b['unidad']}", "fuente": fuente}
        incomparable = False
        if da != db_:
            if {da, db_} <= {"tiempo_h", "dias_habiles"}:
                incomparable = True
                fila["resultado"] = "incomparable_dias_habiles_vs_calendario (se consulta al LLM)"
            else:
                fila["resultado"] = f"incomparable ({da} vs {db_}): no se compara"
                comp_traza.append(fila)
                continue
        elif abs(unidad_a - unidad_b) < 1e-9:
            fila["resultado"] = "iguales: sin conflicto"
            comp_traza.append(fila)
            continue
        else:
            fila["resultado"] = "difieren: se confirma con el LLM"
        comp_traza.append(fila)
        dbd = docs.get(b["doc_id"]) or {}
        por_llm.append({
            "par": i, "a": a, "b": b, "tema": a["tema"], "incomparable": incomparable,
            "va": unidad_a, "vb": unidad_b, "doc_a": doc_id, "doc_b": b["doc_id"],
            "nombre_a": d["nombre_norma"], "nombre_b": dbd.get("nombre_norma"),
            "art_a": a["articulo"], "art_b": b["articulo"], "cond_a": a["condicion"], "cond_b": b["condicion"],
            "valor_a": f"{a['valor']:g}", "valor_b": f"{b['valor']:g}", "unidad_a": a["unidad"],
            "unidad_b": b["unidad"], "texto_a": _texto_chunk(a["chunk_id"]), "texto_b": _texto_chunk(b["chunk_id"]),
            "reemplaza": (dbd.get("codigo") or b["doc_id"]) in reemplazados or b["doc_id"] in reemplazados,
        })

    resultados: dict[int, dict] = {}
    infos = []
    lotes = [por_llm[i:i + TAM_LOTE] for i in range(0, len(por_llm), TAM_LOTE)]
    if lotes:
        with ThreadPoolExecutor(max_workers=min(4, len(lotes))) as ex:
            for res, info in ex.map(lambda l: _confirmar_lote(l, doc_id, usuario), lotes):
                resultados.update(res)
                infos.append(info)
    tokens = sum(i.get("tokens") or 0 for i in infos) or None
    db.traza_ingesta(doc_id, "comparacion", {"n_pares": len(pares), "lotes_llm": len(lotes)},
                     {"pares": comp_traza, "respuestas_llm": resultados,
                      "errores_llm": [i.get("error") for i in infos if i.get("error")]},
                     get_settings().llm_model if lotes else None, tokens,
                     sum(i.get("latencia_ms") or 0 for i in infos) or None, usuario)

    # d) severidad + 7) verificación de citas
    n_reg, bloqueantes = 0, 0
    for p in por_llm:
        a, b = p["a"], p["b"]
        r = resultados.get(p["par"])
        base = {"par": p["par"], "tema": p["tema"], "doc_a": doc_id, "chunk_a": a["chunk_id"], "doc_b": b["doc_id"],
                "chunk_b": b["chunk_id"], "valores": f"{p['valor_a']} {p['unidad_a']} vs {p['valor_b']} {p['unidad_b']}"}
        if not r:
            db.traza_ingesta(doc_id, "discrepancia_descartada", base,
                             {"motivo": "el LLM no devolvió resultado para el par (sin confirmación no se registra)"},
                             usuario=usuario)
            continue
        if not r.get("conflicto") or str(r.get("conflicto")).lower() == "false":
            db.traza_ingesta(doc_id, "discrepancia_descartada", base,
                             {"motivo": f"LLM: no es conflicto — {r.get('explicacion')}"}, usuario=usuario)
            continue
        cita_a = next((c for c in (r.get("cita_a"), a["cita_literal"]) if cita_verificada(c, p["texto_a"])), None)
        cita_b = next((c for c in (r.get("cita_b"), b["cita_literal"]) if cita_verificada(c, p["texto_b"])), None)
        if not cita_a or not cita_b:
            falla = [x for x, ok in (("cita_a", cita_a), ("cita_b", cita_b)) if not ok]
            db.traza_ingesta(doc_id, "discrepancia_descartada",
                             {**base, "cita_a": r.get("cita_a"), "cita_b": r.get("cita_b")},
                             {"motivo": f"cita no verificable: {', '.join(falla)} no aparece literalmente en el "
                                        f"chunk fuente"}, usuario=usuario)
            continue
        if db.query("SELECT 1 FROM discrepancias WHERE tema = ? AND ((chunk_a = ? AND chunk_b = ?) OR "
                    "(chunk_a = ? AND chunk_b = ?))", (p["tema"], a["chunk_id"], b["chunk_id"], b["chunk_id"],
                                                       a["chunk_id"])):
            continue
        dbd = docs[b["doc_id"]]
        cl = clasificar(d, dbd, p["va"], p["vb"], a.get("sentido") or b.get("sentido"), p["reemplaza"])
        if p["incomparable"]:
            cl["motivo"] += " (unidades distintas: días hábiles vs calendario; conflicto confirmado por el LLM)"
        disc_id = db.execute(
            "INSERT INTO discrepancias (tipo, severidad, tema, doc_a, chunk_a, art_a, version_a, cita_a, doc_b, chunk_b,"
            " art_b, version_b, cita_b, prevalece, motivo, explicacion, estado, estado_revision, run_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cl["tipo"], cl["severidad"], p["tema"], doc_id, a["chunk_id"], a["articulo"], d["version"],
             cita_a.strip(), b["doc_id"], b["chunk_id"], b["articulo"], dbd["version"], cita_b.strip(),
             cl["prevalece"], cl["motivo"], str(r.get("explicacion") or "").strip(), "abierta", "pendiente",
             run.id, db.now()))
        n_reg += 1
        bloqueantes += int(cl["bloquea"])
        db.traza_ingesta(doc_id, "discrepancia_confirmada", base,
                         {"id": disc_id, "severidad": cl["severidad"], "tipo": cl["tipo"], "prevalece": cl["prevalece"],
                          "motivo": cl["motivo"], "cita_a": cita_a, "cita_b": cita_b,
                          "cita_fuente": {"a": "llm" if cita_a == r.get("cita_a") else "afirmacion",
                                          "b": "llm" if cita_b == r.get("cita_b") else "afirmacion"},
                          "explicacion": r.get("explicacion")}, usuario=usuario)

    resumen = (f"{len(afs)} afirmaciones, {len(pares)} pares candidatos, {len(por_llm)} consultados al LLM, "
               f"{n_reg} discrepancias registradas ({bloqueantes} ALTA bloqueantes)")
    run.add_step("validador", "final", resumen)
    run.output = resumen
    run.status = "completed"
    run.meta["latencia_ms"] = int((time.perf_counter() - t0) * 1000)
    # La tabla `trazas` (KPI 10) solo recibe validaciones que recuperaron fragmentos del índice o que
    # registraron discrepancias; una validación sin nada que recuperar queda completa en trazas_ingesta.
    guardado = bool(hits_kpi or n_reg)
    if guardado:
        run_store.save(run)
        db.execute("UPDATE documentos SET run_id = ? WHERE doc_id = ?", (run.id, doc_id))
    db.traza_ingesta(doc_id, "validacion_resumen", None, {"resumen": resumen, "run_id": run.id if guardado else None,
                                                          "traza_kpi": guardado}, usuario=usuario)
    return {"run_id": run.id if guardado else None, "n_discrepancias": n_reg, "altas_bloqueantes": bloqueantes, "resumen": resumen}
