"""KPIs del caso B2 normativa, calculados SOLO desde lo que registraron los agentes.

Fuentes: tablas consultas, trazas, discrepancias, documentos y brechas (ver docs/KPIS.md).
Regla: denominador 0 o sin filas con dato -> valor null ("sin datos"). Nada se inventa ni se rellena.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.normas import db

SUPUESTO = "consultas × (tiempo manual de referencia − tiempo promedio del agente)"
AGENTES_CONSULTA = ("consultas", "analista_casos")


def _pct(num: int, den: int) -> dict[str, Any]:
    return {"valor": round(num / den, 4) if den else None, "num": num, "den": den}


def _rango(desde: str | None, hasta: str | None) -> tuple[str | None, str | None]:
    """created_at es ISO local (AAAA-MM-DDTHH:MM:SS). Fecha sola en 'hasta' incluye todo el día."""
    d = desde.strip() if desde and desde.strip() else None
    h = hasta.strip() if hasta and hasta.strip() else None
    if h and len(h) == 10:
        h = h + "T23:59:59.999"
    return d, h


def _where(desde: str | None, hasta: str | None, col: str = "created_at") -> tuple[str, list[Any]]:
    cond, params = [], []
    if desde:
        cond.append(f"{col} >= ?")
        params.append(desde)
    if hasta:
        cond.append(f"{col} <= ?")
        params.append(hasta)
    return (" WHERE " + " AND ".join(cond)) if cond else "", params


def _umbral_backend() -> tuple[str, float]:
    from app.normas import consultas as cons
    from app.normas.vector import get_store
    try:
        backend = get_store().backend
    except Exception:
        backend = "local"
    return backend, cons._umbrales()["U2"]


def _bucket(ts: str, gran: str) -> str:
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return (ts or "")[:13]
    if gran == "dia":
        return t.strftime("%Y-%m-%d")
    if gran == "10min":
        return t.replace(minute=t.minute - t.minute % 10).strftime("%Y-%m-%dT%H:%M")
    return t.strftime("%Y-%m-%dT%H:00")


def _stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"promedio": None, "mediana": None, "n": 0}
    return {"promedio": round(statistics.mean(vals), 2), "mediana": round(statistics.median(vals), 2), "n": len(vals)}


def _sin_cobertura(c: dict[str, Any], umbral: float) -> str | None:
    """'no_encontro' | 'score_bajo' | None (cubierta) | 'sd' (fila sin dato)."""
    if c.get("n_citas_verificadas") is None:
        return "sd"
    if c.get("motivo_sin_cobertura") == "no_encontro" or c["n_citas_verificadas"] == 0:
        return "no_encontro"
    if c.get("top_score") is not None and c["top_score"] < umbral:
        return "score_bajo"
    return None


def _fragmentos_con_score(pasos: list[dict[str, Any]]) -> bool:
    """Al menos un fragmento recuperado con id y score en los resultados de tools."""
    for p in pasos:
        if p.get("type") != "tool_result":
            continue
        data = p.get("data") or {}
        res = data.get("result", data)
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(res, dict):
            continue
        for key in ("hits", "resultados", "fragmentos", "no_vigentes", "candidatos"):
            for h in res.get(key) or []:
                if isinstance(h, dict) and (h.get("id") or h.get("chunk_id")) and any(
                        h.get(k) is not None for k in ("score", "best_score", "rerank_score")):
                    return True
    return False


def calcular(desde: str | None = None, hasta: str | None = None, umbral: float | None = None,
             tiempo_manual_min: float = 15, granularidad: str = "hora") -> dict[str, Any]:
    gran = granularidad if granularidad in ("10min", "hora", "dia") else "hora"
    d, h = _rango(desde, hasta)
    backend, u2 = _umbral_backend()
    umbral = u2 if umbral is None else float(umbral)
    where, params = _where(d, h)

    # ------------------------------------------------------------------ calidad
    consultas = db.query(
        "SELECT id, pregunta, tema, confianza, top_score, citas_json, created_at, tipo, n_citas_propuestas,"
        " n_citas_verificadas, confianza_score, advertencia_conflicto, advertencia_obsoleta,"
        f" motivo_sin_cobertura, area_usuario FROM consultas{where} ORDER BY created_at", params)
    con_dato = [c for c in consultas if c.get("n_citas_verificadas") is not None]
    den = len(con_dato)
    por_tipo = Counter(c["tipo"] or "sin_dato" for c in consultas)
    con_cita = sum(1 for c in con_dato if c["n_citas_verificadas"] >= 1)
    motivos = {c["id"]: _sin_cobertura(c, umbral) for c in consultas}
    no_enc = sum(1 for c in con_dato if motivos[c["id"]] == "no_encontro")
    sc_bajo = sum(1 for c in con_dato if motivos[c["id"]] == "score_bajo")
    adv_rows = [c for c in con_dato if c.get("advertencia_conflicto") is not None]
    conf = sum(1 for c in adv_rows if c["advertencia_conflicto"])
    obs = sum(1 for c in adv_rows if c["advertencia_obsoleta"])
    con_adv = sum(1 for c in adv_rows if c["advertencia_conflicto"] or c["advertencia_obsoleta"])
    scores = [c["confianza_score"] for c in con_dato if c.get("confianza_score") is not None]
    calidad = {
        "total_consultas": len(consultas),
        "por_tipo": dict(por_tipo),
        "con_cita": _pct(con_cita, den),
        "sin_cobertura": {**_pct(no_enc + sc_bajo, den), "no_encontro": no_enc, "score_bajo": sc_bajo},
        "con_advertencia": {**_pct(con_adv, len(adv_rows)), "conflicto": conf, "obsoleta": obs},
        "confianza_promedio": {"valor": round(statistics.mean(scores), 4) if scores else None, "n": len(scores),
                               "distribucion": dict(Counter(c["confianza"] for c in con_dato if c.get("confianza")))},
    }

    # ------------------------------------------------------------------ prevención (estado actual, sin filtro)
    disc = db.query("SELECT tipo, severidad, doc_a, doc_b FROM discrepancias WHERE estado_revision != 'descartada'")
    docs = {x["doc_id"]: x for x in db.query(
        "SELECT doc_id, nombre_norma, estado, reemplaza_a, reemplazada_por FROM documentos")}
    obsoletas = [{"doc_id": x["doc_id"], "nombre_norma": x["nombre_norma"], "estado": x["estado"],
                  "reemplazada_por": x["reemplazada_por"] or next(
                      (o["doc_id"] for o in docs.values() if o["reemplaza_a"] == x["doc_id"]), None)}
                 for x in docs.values() if x["estado"] in ("reemplazada", "derogada")]
    afect: dict[str, set[str]] = defaultdict(set)
    for r in disc:
        if r["doc_a"] and r["doc_b"] and r["doc_a"] != r["doc_b"]:
            afect[r["doc_a"]].add(r["doc_b"])
            afect[r["doc_b"]].add(r["doc_a"])
    for o in obsoletas:
        if o["reemplazada_por"]:
            afect[o["doc_id"]].add(o["reemplazada_por"])
    afectados = sorted(({"doc_id": k, "nombre_norma": (docs.get(k) or {}).get("nombre_norma"), "n": len(v),
                         "afectados": sorted(v)} for k, v in afect.items()), key=lambda x: (-x["n"], x["doc_id"]))
    prevencion = {
        "discrepancias": {"total": len(disc), "por_severidad": {"alta": 0, "media": 0, "informativa": 0,
                                                            **Counter(r["severidad"] for r in disc)},
                          "por_tipo": dict(Counter(r["tipo"] for r in disc))},
        "normas_obsoletas": {"total": len(obsoletas), "lista": obsoletas},
        "afectados_por_norma": afectados,
    }

    # ------------------------------------------------------------------ eficiencia
    tw, tp = _where(d, h)
    trazas = db.query("SELECT run_id, agente, modelo, entrada, salida, pasos_json, estado, started_at, finished_at"
                      f" FROM trazas{tw}", tp)
    dur: dict[str, list[float]] = defaultdict(list)
    for t in trazas:
        try:
            s = (datetime.fromisoformat(t["finished_at"]) - datetime.fromisoformat(t["started_at"])).total_seconds()
        except (TypeError, ValueError):
            continue
        if s >= 0:
            dur[t["agente"] or "sin_agente"].append(s)
    todos = [x for v in dur.values() for x in v]
    agente_vals = [x for a in AGENTES_CONSULTA for x in dur.get(a, [])]
    n_consultas = len(consultas)
    horas = None
    if n_consultas and agente_vals:
        prom_min = statistics.mean(agente_vals) / 60
        horas = round(n_consultas * max(0.0, tiempo_manual_min - prom_min) / 60, 2)
    eficiencia = {
        "tiempo_respuesta_s": {**_stats(todos), "por_agente": {a: _stats(v) for a, v in sorted(dur.items())}},
        "tiempo_ahorrado": {"estimado": True, "tiempo_manual_min": tiempo_manual_min, "consultas": n_consultas,
                            "horas": horas, "supuesto": SUPUESTO},
    }

    # ------------------------------------------------------------------ trazabilidad
    incompletas, completas = [], 0
    for t in trazas:
        if t["estado"] not in ("completed", "failed"):
            continue  # en curso / esperando aprobación: aún no evaluable
        try:
            pasos = json.loads(t["pasos_json"] or "[]")
        except json.JSONDecodeError:
            pasos = []
        faltan = []
        if not (t["entrada"] or "").strip():
            faltan.append("entrada")
        if not _fragmentos_con_score(pasos):
            faltan.append("fragmentos")
        if not (t["salida"] or "").strip():
            faltan.append("salida")
        if not t["modelo"]:
            faltan.append("modelo")
        if not t["started_at"]:
            faltan.append("started_at")
        if not t["finished_at"]:
            faltan.append("finished_at")
        if faltan:
            incompletas.append({"run_id": t["run_id"], "agente": t["agente"], "faltan": faltan})
        else:
            completas += 1
    den_t = completas + len(incompletas)
    # por agente: mismo criterio, para separar el Agente 1 (validador) del Agente 2 (consultas, analista_casos)
    por_ag: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in trazas:
        if t["estado"] in ("completed", "failed"):
            por_ag[t["agente"] or "?"][1] += 1
    for inc in incompletas:
        por_ag[inc["agente"] or "?"][0] += 1
    completas_por_agente = {a: _pct(den - inc_n, den) for a, (inc_n, den) in sorted(por_ag.items())}

    top: Counter = Counter()
    nombres: dict[str, str] = {}
    for c in consultas:
        try:
            citas = json.loads(c["citas_json"] or "[]")
        except json.JSONDecodeError:
            continue
        for doc_id in {x.get("doc_id") for x in citas if isinstance(x, dict) and x.get("doc_id")}:
            top[doc_id] += 1
            nombres[doc_id] = next((x.get("nombre_norma") for x in citas if x.get("doc_id") == doc_id), None) \
                or (docs.get(doc_id) or {}).get("nombre_norma")
    top_normas = [{"doc_id": k, "nombre_norma": nombres.get(k), "n": n} for k, n in top.most_common(10)]

    brechas: dict[str, list[str]] = defaultdict(list)
    for c in consultas:
        if motivos[c["id"]] in ("no_encontro", "score_bajo"):
            brechas[c["tema"] or "otros"].append(c["pregunta"])
    bw, bp = _where(d, h)
    for b in db.query(f"SELECT tema, descripcion FROM brechas{bw}", bp):
        brechas[b["tema"] or "otros"].append(b["descripcion"])
    brechas_l = sorted(({"tema": k, "n": len(v), "ejemplos": v[:3]} for k, v in brechas.items()),
                       key=lambda x: -x["n"])
    trazabilidad = {"trazas_completas": {**_pct(completas, den_t), "incompletas": incompletas,
                                         "por_agente": completas_por_agente},
                    "top_normas": top_normas, "brechas": brechas_l}

    # ------------------------------------------------------------------ tendencia
    buckets: dict[str, dict[str, int]] = {}
    for c in consultas:
        b = buckets.setdefault(_bucket(c["created_at"], gran), {"n": 0, "sin_cobertura": 0})
        b["n"] += 1
        b["sin_cobertura"] += int(motivos[c["id"]] in ("no_encontro", "score_bajo"))
    tendencia = {"granularidad": gran,
                 "consultas": [{"t": k, **v} for k, v in sorted(buckets.items())],
                 "brechas_por_tema": [{"tema": x["tema"], "n": x["n"]} for x in brechas_l]}

    return {"rango": {"desde": desde, "hasta": hasta}, "backend": backend, "umbral": umbral,
            "generado_at": db.now(), "calidad": calidad, "prevencion": prevencion, "eficiencia": eficiencia,
            "trazabilidad": trazabilidad, "tendencia": tendencia,
            "por_area": _por_area(consultas, motivos, d, h)}


SIN_AREA = "sin área"


def _por_area(consultas: list[dict[str, Any]], motivos: dict[int, str | None], d: str | None,
              h: str | None) -> dict[str, Any]:
    """Consultas y brechas por área del USUARIO que preguntó; documentos por área de aplicación de la norma."""
    from app.normas.areas import de_json
    n_cons = Counter(c.get("area_usuario") or SIN_AREA for c in consultas)
    brechas: dict[str, list[str]] = defaultdict(list)
    for c in consultas:  # mismo criterio que el KPI 2 (sin cobertura)
        if motivos[c["id"]] in ("no_encontro", "score_bajo"):
            brechas[c.get("area_usuario") or SIN_AREA].append(c["pregunta"])
    bw, bp = _where(d, h, "b.created_at")
    for b in db.query("SELECT b.descripcion, c.area_usuario FROM brechas b LEFT JOIN consultas c ON c.id = b.consulta_id"
                      f"{bw}", bp):
        brechas[b["area_usuario"] or SIN_AREA].append(b["descripcion"])
    n_docs: Counter = Counter()
    for x in db.query("SELECT areas_aplica FROM documentos WHERE estado = 'vigente' AND publicado = 1"):
        for a in de_json(x["areas_aplica"]) or [SIN_AREA]:
            n_docs[a] += 1
    orden = lambda kv: (-kv[1], kv[0])  # noqa: E731
    return {
        "consultas": [{"area": a, "n": n} for a, n in sorted(n_cons.items(), key=orden)],
        "brechas": sorted(({"area": a, "n": len(v), "ejemplos": v[:3]} for a, v in brechas.items()),
                          key=lambda x: (-x["n"], x["area"])),
        "documentos": [{"area": a, "n": n} for a, n in sorted(n_docs.items(), key=orden)],
    }
