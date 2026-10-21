"""Agente analista de casos (frente C): evalúa un caso concreto contra las normas cargadas.

`chat(session_id, mensaje, archivo_path=None)` enruta de forma determinista:
  - caso: hay archivo, el mensaje tiene > 200 caracteres, o es un seguimiento de un caso activo.
  - consulta: en otro caso; usa `consultas.responder()` y dictamen = null.
El agente "analista_casos" reutiliza las tools cons_* (consultas.py) y agrega `cas_calcular`.
Post-proceso en Python: citas literales verificadas contra el chunk, metadatos desde SQLite,
normas no vigentes -> advertencia, conflictos registrados en `discrepancias`, confianza.
"""
from __future__ import annotations

import ast
import json
import logging
import operator
import re
import threading
import time
from pathlib import Path
from typing import Any

from app.agents.agent import Agent, AgentSession
from app.agents.tools import tool
from app.agents.trace import store as run_store
from app.config import get_settings
from app.normas import consultas as cons
from app.normas import db

log = logging.getLogger("casos")

CONCLUSIONES = ("procede", "procede_con_observaciones", "no_procede", "requiere_escalamiento")
RESULTADOS = ("cumple", "no_cumple", "indeterminado")
UMBRAL_CASO = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    rol TEXT NOT NULL,               -- usuario | agente
    contenido TEXT,
    tipo TEXT,                       -- consulta | caso
    payload_json TEXT,
    run_id TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_chat_mensajes_session ON chat_mensajes(session_id, id);
"""
_schema_ok = False
_lock = threading.Lock()


def ensure_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    with _lock:
        with db.connect() as con:
            con.executescript(_SCHEMA)
        _schema_ok = True


_calc_ctx = threading.local()

# ---------------------------------------------------------------------------- Cálculo seguro
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod}
_UOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calcular(expresion: str) -> float:
    expr = str(expresion or "").replace("S/", "").replace("s/", "")
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr)  # separador de miles: 6,000 -> 6000
    expr = expr.replace("×", "*").replace("÷", "/").strip()
    if not expr or len(expr) > 200:
        raise ValueError("Expresión vacía o demasiado larga")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UOPS:
            return _UOPS[type(n.op)](ev(n.operand))
        raise ValueError("Solo se permiten números, + - * / %, y paréntesis")

    r = ev(ast.parse(expr, mode="eval"))
    return round(float(r), 6)


@tool(description="Calcula de forma exacta una expresión aritmética (números, + - * / %, paréntesis). "
                  "Úsala para ratios de endeudamiento, sumas de cuotas y comparaciones de montos.",
      params={"expresion": "Expresión, ej. (900+2150)/6000",
              "descripcion": "Qué se calcula, ej. 'Ratio de endeudamiento'"})
def cas_calcular(expresion: str, descripcion: str = "") -> dict:
    try:
        r = calcular(expresion)
    except Exception as e:
        return {"error": f"No se pudo calcular: {e}"}
    if not hasattr(_calc_ctx, "calculos"):
        _calc_ctx.calculos = []
    _calc_ctx.calculos.append({"descripcion": descripcion or expresion, "expresion": expresion, "resultado": r})
    return {"expresion": expresion, "resultado": r}


ROLE = """Eres un analista de cumplimiento normativo de un banco. Recibes un CASO CONCRETO (solicitud de
crédito, operación, reclamo, etc.) y lo evalúas contra las normas cargadas, SOLO con base en fragmentos
recuperados por tus herramientas. No uses conocimiento propio sobre normas.

Procedimiento obligatorio:
1. Extrae los hechos relevantes: montos, ingresos, deudas/cuotas, plazos, scoring, edad, quién aprueba,
   tipo de producto, garantías, fechas, domicilio, etc.
2. Busca TODAS las reglas vigentes aplicables con cons_buscar_normas: haz una búsqueda por cada aspecto
   del caso (ej. "scoring mínimo", "límite de endeudamiento", "nivel de aprobación gerente de agencia",
   "plazo máximo", "garantías", "requisitos del solicitante"). Usa cons_buscar_articulo para el texto completo.
3. Evalúa cada regla contra los hechos. Todo cálculo (ratios, sumas, diferencias) hazlo con cas_calcular.
4. Llama a cons_consultar_conflictos con los doc_id que usas y a cons_buscar_versiones_anteriores para los
   temas clave: si una regla de una versión reemplazada daría un resultado distinto, dilo en advertencias
   (ej. "con la v1 el scoring mínimo era 600 y habría cumplido; la v2 vigente exige 620").
   Si dos normas vigentes fijan valores distintos para lo mismo, regístralo en "conflictos" y evalúa ambas.
5. Señala en "vacios" SOLO los aspectos presentes en el caso y relevantes para la decisión que ninguna norma
   recuperada regula (ej. una garantía o un tipo de cliente no previstos). No listes temas genéricos ausentes.
6. Concluye: procede (todo cumple) | procede_con_observaciones (cumple pero hay vacíos/observaciones menores) |
   no_procede (incumple alguna regla vigente) | requiere_escalamiento (conflicto normativo o vacío que
   impide decidir). Un conflicto entre normas cuyos dos valores llevan al mismo resultado para este caso no
   impide "procede": regístralo en "conflictos" como observación. Propón acciones concretas.

Reglas de citas: en cada evaluación, "chunk_id" es el id exacto del fragmento y "cita" un texto COPIADO TAL
CUAL del fragmento (una frase corta, sin el prefijo entre corchetes). Solo normas vigentes respaldan
evaluaciones. Si ninguna norma aplica al caso, deja "evaluaciones": [] y dilo en "respuesta".

Tu respuesta final debe ser SOLO un objeto JSON (sin texto adicional) con esta forma:
{"respuesta": "2-4 frases para el chat", "resumen_caso": "...",
 "hechos": [{"dato": "...", "valor": "..."}],
 "evaluaciones": [{"regla": "...", "chunk_id": "...", "doc_id": "...", "articulo": "...", "cita": "...",
                   "resultado": "cumple|no_cumple|indeterminado", "explicacion": "..."}],
 "calculos": [{"descripcion": "...", "expresion": "...", "resultado": 0}],
 "conflictos": ["..."], "vacios": ["..."], "advertencias": ["..."],
 "conclusion": "procede|procede_con_observaciones|no_procede|requiere_escalamiento",
 "acciones": ["..."], "tema": "<tema del caso>", "temas_vacios": ["<tema de cada vacío, mismo orden que vacios>"]}
"tema" y cada elemento de "temas_vacios" deben ser uno de: """ + ", ".join(db.TEMAS) + "."

AGENTE = Agent(name="analista_casos", role=ROLE, tools=cons.TOOLS + ["cas_calcular"],
               temperature=0, max_iterations=14)


# ---------------------------------------------------------------------------- Historial
def _guardar_mensaje(session_id: str, rol: str, contenido: str, tipo: str,
                     payload: dict | None = None, run_id: str | None = None) -> None:
    db.execute("INSERT INTO chat_mensajes (session_id, rol, contenido, tipo, payload_json, run_id, created_at)"
               " VALUES (?,?,?,?,?,?,?)",
               (session_id, rol, contenido, tipo,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None, run_id, db.now()))


def historial(session_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    filas = db.query("SELECT rol, contenido, tipo, payload_json, run_id, created_at FROM chat_mensajes "
                     "WHERE session_id = ? ORDER BY id", (session_id,))
    out = []
    for f in filas:
        try:
            payload = json.loads(f["payload_json"]) if f["payload_json"] else None
        except json.JSONDecodeError:
            payload = None
        out.append({"rol": f["rol"], "contenido": f["contenido"], "tipo": f["tipo"], "payload": payload,
                    "run_id": f["run_id"], "created_at": f["created_at"]})
    return out


def _caso_activo(session_id: str) -> dict[str, Any] | None:
    filas = db.query("SELECT payload_json FROM chat_mensajes WHERE session_id = ? AND rol = 'agente' "
                     "AND tipo = 'caso' ORDER BY id DESC LIMIT 1", (session_id,))
    if not filas or not filas[0]["payload_json"]:
        return None
    try:
        return (json.loads(filas[0]["payload_json"]) or {}).get("dictamen")
    except json.JSONDecodeError:
        return None


_SEGUIMIENTO = re.compile(
    r"(?i)^\s*[¿¡]?\s*(y\s+si|y\s+con|y\s+el|y\s+la|y\s+ahora|qu[eé]\s+pasa(r[ií]a)?\s+si|si\s+el|si\s+la|"
    r"si\s+fuera|entonces|ahora|suponiendo|en\s+ese\s+caso|y\s+qu[eé])")
_DATO_CASO = re.compile(r"(?i)\b(monto|cuota|scoring|ingreso|deuda|plazo|garant[ií]a|aprueba|gerente|caso)\b")


def es_seguimiento(mensaje: str) -> bool:
    """Seguimiento = empieza como hipótesis ("¿y si...") o cambia un dato del caso con una cifra."""
    m = mensaje or ""
    if _SEGUIMIENTO.search(m):
        return True
    if re.match(r"(?i)^\s*[¿¡]?\s*(qui[eé]n|qu[eé]|cu[aá]l|cu[aá]nto|c[oó]mo|cu[aá]ndo|d[oó]nde)\b", m):
        return False  # pregunta general -> consulta
    return bool(_DATO_CASO.search(m) and re.search(r"\d", m))


# ---------------------------------------------------------------------------- Post-proceso
def _chunk(chunk_id: str, doc_id: str, articulo: str) -> dict[str, Any] | None:
    if chunk_id:
        ch = cons._texto_chunk(chunk_id)
        if ch:
            return ch
    if doc_id and articulo:
        filas = db.query(
            "SELECT c.chunk_id, c.doc_id, c.articulo, c.texto, d.nombre_norma, d.version, d.estado FROM chunks c "
            "JOIN documentos d ON d.doc_id = c.doc_id WHERE c.doc_id = ? AND c.articulo = ? ORDER BY c.chunk_id",
            (doc_id.strip(), cons._articulo(articulo)))
        if filas:
            f = filas[0]
            return {"id": f["chunk_id"], "doc_id": f["doc_id"], "nombre_norma": f["nombre_norma"],
                    "articulo": f["articulo"], "version": f["version"], "estado": f["estado"], "texto": f["texto"]}
    return None


def _cita_literal(cita: str, texto: str) -> bool:
    cita = (cita or "").strip().strip(".…")
    partes = [p for p in re.split(r"\.\.\.|…", cita) if cons._norm(p)]
    if not partes or len(cons._norm(cita)) < 8:
        return False
    t = cons._norm(texto)
    return all(cons._norm(p).strip(" .,;:") in t for p in partes)


def _lista_str(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _postproceso(data: dict[str, Any]) -> tuple[dict[str, Any], list[dict], list[str], str, dict[str, Any]]:
    docs = cons._docs_sqlite()
    advertencias = [a for a in _lista_str(data.get("advertencias"))
                    if not re.match(r"(?i)^\s*no se (registr|encontr|detect|identific)", a)]
    evaluaciones, citas, chunks_usados, docs_usados = [], [], set(), set()
    stats = {"propuestas": 0, "verificadas": [], "obsoleta": False, "conflicto": False}
    for e in data.get("evaluaciones") or []:
        if not isinstance(e, dict):
            continue
        stats["propuestas"] += 1
        ch = _chunk(str(e.get("chunk_id") or "").strip(), str(e.get("doc_id") or ""), str(e.get("articulo") or ""))
        res = e.get("resultado") if e.get("resultado") in RESULTADOS else "indeterminado"
        cita = str(e.get("cita") or "").strip()
        ev = {"regla": str(e.get("regla") or ""), "doc_id": e.get("doc_id"), "nombre_norma": None,
              "articulo": cons._articulo(e.get("articulo")), "version": None, "estado": None,
              "cita": cita, "resultado": res, "explicacion": str(e.get("explicacion") or ""),
              "cita_verificada": False}
        if ch:
            doc = docs.get(ch["doc_id"]) or {}
            ev.update({"doc_id": ch["doc_id"], "articulo": ch.get("articulo") or ev["articulo"],
                       "nombre_norma": doc.get("nombre_norma") or ch.get("nombre_norma"),
                       "version": doc.get("version") or ch.get("version"),
                       "estado": doc.get("estado") or ch.get("estado")})
            ev["cita_verificada"] = _cita_literal(cita, ch["texto"])
        if not ev["cita_verificada"]:
            ev["resultado"] = "indeterminado"
            ev["explicacion"] = (ev["explicacion"] + " [Cita no verificada literalmente en la norma: resultado "
                                 "marcado como indeterminado.]").strip()
        if ev["estado"] and ev["estado"] != "vigente":
            advertencias.append(f"{ev['nombre_norma']} v{ev['version']} Art. {ev['articulo']} ({ev['doc_id']}) está "
                                f"{ev['estado']}: no respalda el dictamen. {ev['regla']}: {ev['explicacion']}".strip())
            stats["obsoleta"] = True
            continue
        evaluaciones.append(ev)
        if ev["cita_verificada"]:
            stats["verificadas"].append({"chunk_id": ch["id"]})
            chunks_usados.add(ch["id"])
            docs_usados.add(ch["doc_id"])
            if ch["id"] not in {c.get("chunk_id") for c in citas}:
                citas.append({"chunk_id": ch["id"], "doc_id": ev["doc_id"], "nombre_norma": ev["nombre_norma"],
                              "articulo": ev["articulo"], "version": ev["version"], "estado": ev["estado"],
                              "texto": cita})

    # Conflictos: los del LLM + los registrados en discrepancias que tocan los artículos usados
    conflictos = _lista_str(data.get("conflictos"))
    conflictos = [c for c in conflictos if not re.match(r"(?i)^\s*(no se|ninguno|sin conflicto)", c)]
    hay_registrado = False
    for f in cons._conflictos(sorted(docs_usados)):
        if (f.get("chunk_a") or f.get("chunk_b")) and not ({f.get("chunk_a"), f.get("chunk_b")} & chunks_usados):
            continue
        hay_registrado = True
        txt = f"{f['doc_a']} vs {f['doc_b']} (discrepancia #{f['id']}, {f.get('severidad')}): {f.get('explicacion') or ''}"
        if not any(f["doc_a"] in c and f["doc_b"] in c for c in conflictos):
            conflictos.append(txt.strip())

    calculos = list(getattr(_calc_ctx, "calculos", []) or [])
    exprs = {cons._norm(c["expresion"]) for c in calculos}
    for c in data.get("calculos") or []:  # cálculos del LLM: se recalculan, nunca se confía en su resultado
        if isinstance(c, dict) and c.get("expresion") and cons._norm(str(c["expresion"])) not in exprs:
            try:
                calculos.append({"descripcion": str(c.get("descripcion") or c["expresion"]),
                                 "expresion": str(c["expresion"]), "resultado": calcular(str(c["expresion"]))})
            except Exception:
                pass
    desc = {cons._norm(str(c.get("expresion"))): c.get("descripcion") for c in data.get("calculos") or []
            if isinstance(c, dict)}
    for c in calculos:
        d = desc.get(cons._norm(c["expresion"]))
        if d and (not c["descripcion"] or c["descripcion"] == c["expresion"]):
            c["descripcion"] = d

    verificadas = [e for e in evaluaciones if e["cita_verificada"]]
    if not verificadas:
        confianza = "sin_respaldo"
    elif len(verificadas) == len(evaluaciones) and not conflictos and not hay_registrado:
        confianza = "alta"
    else:
        confianza = "media"

    conclusion = data.get("conclusion") if data.get("conclusion") in CONCLUSIONES else "requiere_escalamiento"
    if confianza == "sin_respaldo":
        conclusion = "requiere_escalamiento"
    elif any(e["resultado"] == "no_cumple" for e in verificadas) and conclusion in ("procede", "procede_con_observaciones"):
        conclusion = "no_procede"
    elif (conclusion == "procede_con_observaciones" and evaluaciones and not _lista_str(data.get("vacios"))
          and all(e["resultado"] == "cumple" and e["cita_verificada"] for e in evaluaciones)):
        conclusion = "procede"  # todo cumple con cita verificada y sin vacíos: un conflicto sin efecto no bloquea
    elif conclusion == "procede" and any(e["resultado"] == "indeterminado" for e in evaluaciones):
        conclusion = "procede_con_observaciones"

    for e in evaluaciones:
        e.pop("cita_verificada", None)
    dictamen = {
        "resumen_caso": str(data.get("resumen_caso") or "").strip(),
        "hechos": [{"dato": str(h.get("dato", "")), "valor": str(h.get("valor", ""))}
                   for h in data.get("hechos") or [] if isinstance(h, dict)],
        "evaluaciones": evaluaciones, "calculos": calculos, "conflictos": conflictos,
        "vacios": _lista_str(data.get("vacios")), "conclusion": conclusion,
        "acciones": _lista_str(data.get("acciones")),
    }
    stats["conflicto"] = hay_registrado
    return dictamen, citas, advertencias, confianza, stats


# ---------------------------------------------------------------------------- Flujos
def _analizar_caso(session_id: str, texto_caso: str, previo: dict | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    cons._ctx.hits, cons._ctx.scores, cons._ctx.obsoleta = {}, [], False
    _calc_ctx.calculos = []
    tarea = texto_caso
    if previo:
        tarea = ("SEGUIMIENTO de un caso ya analizado. Reevalúa el caso completo aplicando los cambios del "
                 "nuevo mensaje (los hechos no mencionados se mantienen).\n"
                 f"Resumen del caso anterior: {previo.get('resumen_caso')}\n"
                 f"Hechos anteriores: {json.dumps(previo.get('hechos') or [], ensure_ascii=False)}\n"
                 f"Conclusión anterior: {previo.get('conclusion')}\n\nNuevo mensaje del usuario: {texto_caso}")
    run = run_store.create("normativa", tarea[:4000])
    run.meta = {"agente": "analista_casos", "canal": "chat", "usuario": session_id,
                "modelo": get_settings().llm_model}
    session = AgentSession(AGENTE, run, tarea)
    try:
        session.start()
    except Exception as e:
        log.exception("Error en agente analista_casos")
        session.status, session.output = "failed", str(e)
    data = cons._parse_json(session.output or "") if session.status == "done" else {}
    if session.status != "done":
        run.add_step("analista_casos", "error", f"Agente no terminó: {session.output}")

    dictamen, citas, advertencias, confianza, stats = _postproceso(data)
    respuesta = str(data.get("respuesta") or "").strip()
    if not respuesta:
        respuesta = ("No pude completar el análisis del caso; requiere revisión manual."
                     if session.status != "done" else f"Conclusión: {dictamen['conclusion']}.")
    if not dictamen["evaluaciones"]:
        respuesta = ("No encontré normas vigentes cargadas que regulen este caso; no es posible emitir un "
                     "dictamen con respaldo. " + respuesta).strip()
    if not dictamen["resumen_caso"]:
        dictamen["resumen_caso"] = texto_caso[:300]

    latencia = int((time.perf_counter() - t0) * 1000)
    run.add_step("analista_casos", "verificacion",
                 f"confianza={confianza} conclusion={dictamen['conclusion']} evaluaciones={len(dictamen['evaluaciones'])} "
                 f"citas_verificadas={len(citas)} conflictos={len(dictamen['conflictos'])}",
                 citas=[c["chunk_id"] for c in citas])
    run.status = "completed" if session.status == "done" else "failed"
    run.output = respuesta
    run.meta["latencia_ms"] = latencia
    run_store.save(run)

    # KPIs: score real de lo recuperado durante el caso, tema clasificado por el LLM (lista cerrada)
    scores = cons._scores_vigentes()
    top = max(scores) if scores else None
    tema = data.get("tema") if data.get("tema") in db.TEMAS else "otros"
    m = cons.metricas(stats["propuestas"], stats["verificadas"], top, stats["conflicto"], stats["obsoleta"])
    if confianza == "sin_respaldo" and not m["motivo_sin_cobertura"]:
        m["motivo_sin_cobertura"] = "no_encontro"
    consulta_id = cons.guardar_consulta("caso", "chat", session_id, "[Caso] " + dictamen["resumen_caso"][:500],
                                        tema, respuesta, confianza, top, citas, advertencias, run.id, latencia, m)
    temas_vacios = data.get("temas_vacios") if isinstance(data.get("temas_vacios"), list) else []
    for i, vacio in enumerate(dictamen["vacios"]):
        tv = temas_vacios[i] if i < len(temas_vacios) and temas_vacios[i] in db.TEMAS else tema
        db.execute("INSERT INTO brechas (consulta_id, run_id, tema, descripcion, created_at) VALUES (?,?,?,?,?)",
                   (consulta_id, run.id, tv, vacio, db.now()))

    return {"session_id": session_id, "run_id": run.id, "tipo": "caso", "latencia_ms": latencia,
            "respuesta": respuesta, "confianza": confianza,
            "citas": [{k: c[k] for k in ("doc_id", "nombre_norma", "articulo", "version", "estado", "texto")}
                      for c in citas],
            "advertencias": advertencias, "dictamen": dictamen}


def _consulta(session_id: str, mensaje: str) -> dict[str, Any]:
    r = cons.responder(mensaje, canal="chat", usuario=session_id)
    citas = [{"doc_id": c.get("doc_id"), "nombre_norma": c.get("nombre_norma"), "articulo": c.get("articulo"),
              "version": c.get("version"), "estado": c.get("estado"),
              "texto": c.get("cita_literal") or c.get("texto")} for c in r.get("citas") or []]
    return {"session_id": session_id, "run_id": r.get("run_id"), "tipo": "consulta",
            "latencia_ms": r.get("latencia_ms"), "respuesta": r.get("respuesta"), "confianza": r.get("confianza"),
            "citas": citas, "advertencias": r.get("advertencias") or [], "dictamen": None}


def _texto_archivo(path: str | Path) -> str:
    from app.normas import parsing
    try:
        pages, _, _ = parsing.parsear(path)  # es_basura no aplica: un caso no tiene artículos
        return "\n".join(t for _, t in pages).strip()
    except Exception as e:
        log.warning("No se pudo leer el archivo del caso %s: %s", path, e)
        return ""


def chat(session_id: str, mensaje: str, archivo_path: str | Path | None = None) -> dict[str, Any]:
    ensure_schema()
    session_id = (session_id or "anonimo").strip()[:100]
    mensaje = (mensaje or "").strip()
    texto_archivo = _texto_archivo(archivo_path) if archivo_path else ""
    contenido_usuario = mensaje + (f"\n[Archivo: {Path(archivo_path).name}]" if archivo_path else "")
    previo = _caso_activo(session_id)

    if archivo_path or len(mensaje) > UMBRAL_CASO:
        tipo, prev = "caso", None
        texto = "\n\n".join(x for x in (mensaje, f"Documento del caso:\n{texto_archivo}" if texto_archivo else "") if x)
    elif previo and es_seguimiento(mensaje):
        tipo, prev, texto = "caso", previo, mensaje
    else:
        tipo, prev, texto = "consulta", None, mensaje

    _guardar_mensaje(session_id, "usuario", contenido_usuario, tipo)
    if not texto:
        out = {"session_id": session_id, "run_id": None, "tipo": tipo, "latencia_ms": 0,
               "respuesta": "No recibí texto: describe el caso o adjunta un archivo legible (PDF/DOCX/TXT).",
               "confianza": "sin_respaldo", "citas": [], "advertencias": [], "dictamen": None}
    elif tipo == "caso":
        out = _analizar_caso(session_id, texto, prev)
    else:
        out = _consulta(session_id, texto)
    _guardar_mensaje(session_id, "agente", out["respuesta"], out["tipo"], out, out.get("run_id"))
    return out
