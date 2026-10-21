"""Verifica el catálogo de casos de uso del Agente 1 (normas/CASOS_DE_USO.md) contra el código real.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verificar_casos_uso.py

ATENCIÓN: hace reset (documentos/chunks/afirmaciones/discrepancias/trazas_ingesta de la SQLite local y el
namespace ACTUAL del índice) dos veces: escenario 1 (orden inverso: 09 antes que 31, 01 después de 31) y
escenario 2 (resto de casos). Se niega a correr si PINECONE_NAMESPACE es 'demo' (el del VPS).
Los archivos nuevos se generan con scripts/generar_casos_uso.py. Al terminar NO deja la base de la demo:
vuelve a sembrar con  scripts/seed_corpus.py --reset --normas-demo --hasta-base
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.normas import db  # noqa: E402
from app.normas.areas import AREAS, de_json  # noqa: E402
from app.normas.ingesta import ArchivoRechazado, ingerir  # noqa: E402
from app.normas.vector import get_store  # noqa: E402
from scripts.aceptacion_agente1 import discs, doc, estados_indice  # noqa: E402
from scripts.seed_corpus import cargar, reset  # noqa: E402

N = ROOT / "normas"
C = N / "casos_uso"
RES: list[dict] = []


def f(pref: str) -> Path:
    """'09' -> normas/09_….pdf ; 'CU14' -> normas/casos_uso/CU14_…"""
    base = C if pref.startswith("CU") else N
    m = sorted(base.glob(f"{pref}_*"))
    if not m:
        raise FileNotFoundError(f"falta {pref}_* en {base} (corre scripts/generar_casos_uso.py)")
    return m[0]


def rel(*prefs: str) -> str:
    return ", ".join(str(f(p).relative_to(N)).replace("\\", "/") for p in prefs)


def caso(n: str, archivos: str, esperado: str, fn) -> None:
    """fn() -> (True | False | None, obtenido). None = NO SOPORTADO."""
    try:
        ok, obt = fn()
    except Exception as e:  # el caso no debe tumbar la verificación
        ok, obt = False, f"excepción: {type(e).__name__}: {e}"
    est = "NO SOPORTADO" if ok is None else ("PASS" if ok else "FAIL")
    RES.append({"caso": n, "archivos": archivos, "esperado": esperado, "obtenido": obt, "resultado": est})
    print(f"   -> [{est}] CU{n}: {obt}")


def md(d: dict) -> dict:
    return json.loads(d.get("metadatos_json") or "{}")


def traza(doc_id: str, paso: str) -> list:
    out = []
    for r in db.query("SELECT salida FROM trazas_ingesta WHERE doc_id = ? AND paso = ? ORDER BY id", (doc_id, paso)):
        try:
            out.append(json.loads(r["salida"]) if r["salida"] else {})
        except ValueError:
            out.append({"raw": r["salida"]})
    return out


def pares(doc_id: str) -> list[dict]:
    return [p for t in traza(doc_id, "comparacion") for p in (t.get("pares") or [])]


def sev(doc_id: str) -> str:
    rows = db.query("SELECT severidad, doc_b, COALESCE(estado,'abierta') est FROM discrepancias WHERE doc_a = ? "
                    "ORDER BY id", (doc_id,))
    return ", ".join(f"{r['severidad']}→{r['doc_b']}({r['est']})" for r in rows) or "sin discrepancias"


def resumen_doc(d: dict | None) -> str:
    if not d:
        return "documento no encontrado"
    return (f"estado={d['estado']} publicado={d['publicado']} ingesta={d['estado_ingesta']} "
            f"inferido={d['estado_inferido']} req_conf={d['requiere_confirmacion']} chunks={d['n_chunks']}")


_CLIENT = None


def api():
    global _CLIENT
    if _CLIENT is None:
        from fastapi.testclient import TestClient
        from app.main import app
        for nombre in ("", "llm", "trace", "ingesta", "app"):  # app.main reconfigura el logging a INFO
            logging.getLogger(nombre).setLevel(logging.ERROR)
        _CLIENT = TestClient(app)
    return _CLIENT


def areas_indice(doc_id: str, esperado: list[str], intentos: int = 8) -> list:
    """areas_aplica de los chunks de doc_id en el índice (reintenta por la consistencia eventual de Pinecone)."""
    vistos: list = []
    for _ in range(intentos):
        hits = get_store().search("norma artículo", top_k=50, filter={"doc_id": doc_id})
        vistos = sorted({tuple(h.metadata.get("areas_aplica") or []) for h in hits})
        if hits and vistos == [tuple(esperado)]:
            break
        time.sleep(2)
    return [list(v) for v in vistos]


def areas_doc(d: dict | None) -> str:
    if not d:
        return "documento no encontrado"
    return (f"areas={de_json(d['areas_aplica'])} fuente={d['areas_fuente']} conf={d['areas_confianza']} "
            f"areas_req={d['areas_requiere_confirmacion']} req_conf_doc={d['requiere_confirmacion']}")


def subir_api(path: Path, nombre: str | None = None):
    return api().post("/api/documentos", files={"archivo": (nombre or path.name, path.read_bytes())})


def decidir_api(doc_id: str, decision: str):
    return api().post(f"/api/documentos/{doc_id}/decision",
                      json={"decision": decision, "usuario": "verificacion", "comentario": f"CU {decision}"})


# ============================================================================ escenario 1 (orden inverso)
def escenario_1() -> None:
    print("\n== Escenario 1 (reset): 09 -> 31 -> 01 ==")
    reset()
    cargar(f("09"))
    cargar(f("31"))

    def cu13():
        d31, d09 = doc("RES-SINT-2024-001"), doc("MAN-CR-2025-004")
        altas = discs(doc_a="RES-SINT-2024-001", doc_b="MAN-CR-2025-004", severidad="alta")
        abiertas = [x for x in altas if (x["estado"] or "abierta") == "abierta"]
        ok = d31["publicado"] == 1 and d31["estado"] == "vigente" and len(abiertas) >= 1
        return ok, (f"31: {resumen_doc(d31)}; ALTA 31→09 abiertas={len(abiertas)} temas="
                    f"{sorted({x['tema'] for x in abiertas})} prevalece={sorted({x['prevalece'] for x in abiertas})}; "
                    f"09 sigue estado={d09['estado']} publicado={d09['publicado']}")
    caso("13", rel("09", "31"), "31 se publica (vigente); ALTA 31↔09 abierta, prevalece 31; 09 no se bloquea", cu13)

    cargar(f("01"))

    def cu4():
        d = doc("RES-SINT-2019-014")
        t = traza("RES-SINT-2019-014", "reemplazo")
        comp = traza("RES-SINT-2019-014", "comparacion")
        ok = d["estado"] == "reemplazada" and d["reemplazada_por"] == "RES-SINT-2024-001"
        return ok, (f"{resumen_doc(d)} reemplazada_por={d['reemplazada_por']}; traza reemplazo="
                    f"{[x.get('motivo') for x in t]}; comparación omitida={[x.get('omitida') for x in comp]}")
    caso("4", rel("31", "01"), "01 cargada después de 31 queda 'reemplazada' desde el inicio (sin comparar)", cu4)


# ============================================================================ escenario 2
def escenario_2() -> None:
    print("\n== Escenario 2 (reset): flujo principal ==")
    reset()
    st = get_store()

    # ---------------------------------------------------------------- A. flujo normal
    cargar(f("02"))

    def cu1():
        d = doc("RES-SINT-2023-009")
        n = len(discs(doc_a="RES-SINT-2023-009"))
        ok = d["estado"] == "vigente" and d["publicado"] == 1 and d["tipo_fuente"] == "externa" and n == 0
        return ok, f"{resumen_doc(d)} tipo={d['tipo_fuente']} discrepancias={n}"
    caso("1", rel("02"), "externa, vigente, publicada, sin discrepancias", cu1)

    cargar(f("25"))

    def cu2():
        d = doc("POL-PDP-2024-020")
        n = len(discs(doc_a="POL-PDP-2024-020"))
        ok = d["estado"] == "vigente" and d["publicado"] == 1 and d["tipo_fuente"] == "interna" and n == 0
        return ok, f"{resumen_doc(d)} tipo={d['tipo_fuente']} discrepancias={n}"
    caso("2", rel("25"), "interna, vigente, publicada, sin discrepancias", cu2)

    # ---------------------------------------------------------------- B3. nuevas versiones
    for p in ("01", "31", "07", "08"):
        cargar(f(p))

    def cu3():
        d01, d07 = doc("RES-SINT-2019-014"), doc("POL-RC-2022-001")
        i01 = estados_indice("RES-SINT-2019-014", "reemplazada")
        i07 = estados_indice("POL-RC-2022-001", "reemplazada")
        ok = (d01["estado"] == "reemplazada" and d01["reemplazada_por"] == "RES-SINT-2024-001" and i01 == {"reemplazada"}
              and d07["estado"] == "reemplazada" and d07["reemplazada_por"] == "POL-RC-2025-002"
              and i07 == {"reemplazada"})
        return ok, (f"01 estado={d01['estado']} por={d01['reemplazada_por']} índice={i01}; 07 estado={d07['estado']} "
                    f"por={d07['reemplazada_por']} índice={i07}; 31 {resumen_doc(doc('RES-SINT-2024-001'))}; "
                    f"08 {resumen_doc(doc('POL-RC-2025-002'))}")
    caso("3", rel("01", "31", "07", "08"), "01→reemplazada por 31 y 07→reemplazada por 08 (SQLite e índice)", cu3)

    # precondiciones de C
    for p in ("03", "05", "17", "11"):
        cargar(f(p))

    def cu11():
        d = doc("MAN-HIP-2024-006")
        info = discs(doc_a="MAN-HIP-2024-006", severidad="informativa")
        graves = [x for x in discs(doc_a="MAN-HIP-2024-006") if x["severidad"] in ("alta", "media")]
        ok = d["publicado"] == 1 and not graves and any(x["doc_b"] == "RES-SINT-2023-018" for x in info)
        return ok, (f"{resumen_doc(d)}; informativas={[(x['tema'], x['doc_b'], x['tipo']) for x in info]}; "
                    f"alta/media={len(graves)}")
    caso("11", rel("05", "11"), "INFORMATIVA (interna más estricta) vs 05; se publica", cu11)

    cargar(f("09"))

    def cu9():
        d = doc("MAN-CR-2025-004")
        altas = discs(doc_a="MAN-CR-2025-004", doc_b="RES-SINT-2024-001", severidad="alta")
        idx = estados_indice("MAN-CR-2025-004", "pendiente_revision", 4)
        ok = (d["estado"] == "pendiente_revision" and d["publicado"] == 0 and len(altas) >= 1
              and idx == {"pendiente_revision"})
        return ok, (f"{resumen_doc(d)}; ALTA vs 31 temas={sorted({x['tema'] for x in altas})}; "
                    f"estado en índice={idx}")
    caso("9", rel("31", "09"), "ALTA vs 31; pendiente_revision, publicado=0, índice 'pendiente_revision'", cu9)

    cargar(f("18"))

    def cu10():
        d = doc("CI-2025-013")
        medias = discs(doc_a="CI-2025-013", severidad="media")
        contra = {x["doc_b"] for x in medias}
        ok = {"POL-RC-2025-002", "CI-2025-012"} <= contra and d["publicado"] == 1
        return ok, (f"{resumen_doc(d)}; MEDIA contra {sorted(contra)} prevalece="
                    f"{sorted({x['prevalece'] for x in medias})}")
    caso("10", rel("08", "17", "18"), "MEDIA vs 08 y 17 (internas, prevalece la más reciente: 18); se publica", cu10)

    # El PDF 18 tiene un único artículo de 117 caracteres sin espacios (< MIN_CHARS=120 tras quitar la ficha):
    # se repite el caso con la fuente .md equivalente (su título y aviso sí cuentan como texto).
    r10 = cargar(ROOT / "normas_demo" / "18_ci-2025-013.md")

    def cu10b():
        d = doc(r10["doc_id"])
        medias = discs(doc_a=r10["doc_id"], severidad="media")
        contra = {x["doc_b"] for x in medias}
        ok = {"POL-RC-2025-002", "CI-2025-012"} <= contra and d["publicado"] == 1
        return ok, (f"doc_id={r10['doc_id']} {resumen_doc(d)}; MEDIA contra {sorted(contra)} prevalece="
                    f"{sorted({x['prevalece'] for x in medias})}")
    caso("10b", "../normas_demo/18_ci-2025-013.md", "variante .md de 18: MEDIA vs 08 y 17; se publica", cu10b)

    cargar(f("06"))

    def cu12():
        d = doc("RES-SINT-2025-003")
        m = discs(doc_a="RES-SINT-2025-003", doc_b="RES-SINT-2024-001", tema="refinanciacion_plazo")
        ok = d["publicado"] == 1 and any(x["severidad"] == "media" and x["prevalece"] == "RES-SINT-2025-003" for x in m)
        return ok, (f"{resumen_doc(d)}; vs 31: {[(x['severidad'], x['prevalece']) for x in m]}; "
                    f"31 sigue {doc('RES-SINT-2024-001')['estado']}")
    caso("12", rel("31", "06"), "MEDIA externa vs externa, prevalece 06 (más reciente); 06 se publica", cu12)

    # ---------------------------------------------------------------- C14-16 normalización
    cargar(f("CU14"))

    def cu14():
        d = doc("PRO-ROS-2026-105")
        ps = pares("PRO-ROS-2026-105")
        iguales = [p for p in ps if str(p.get("resultado", "")).startswith("iguales")]
        n = len(discs(doc_a="PRO-ROS-2026-105"))
        ok = n == 0 and d["publicado"] == 1 and any("RES-SINT-2022-031" in p["b"] for p in iguales)
        return ok, (f"{resumen_doc(d)} discrepancias={n}; pares: " +
                    "; ".join(f"{p['a'].split(': ')[1]} vs {p['b']} = {p['resultado']}" for p in ps))
    caso("14", rel("03", "31", "CU14"), "1 día calendario = 24 horas (03) y 120 meses = 10 años (31): "
                                        "'iguales', sin discrepancia", cu14)

    cargar(f("CU16"))

    def cu16():
        d = doc("POL-EXP-2026-107")
        ps = pares("POL-EXP-2026-107")
        n = len(discs(doc_a="POL-EXP-2026-107"))
        ok = n == 0 and d["publicado"] == 1 and any(str(p.get("resultado", "")).startswith("iguales") and
                                                    "RES-SINT-2024-001" in p["b"] for p in ps)
        return ok, (f"{resumen_doc(d)} discrepancias={n}; pares: " +
                    "; ".join(f"{p['a'].split(': ')[1]} vs {p['b']} = {p['resultado']}" for p in ps))
    caso("16", rel("31", "08", "CU16"), "40% y 10 años iguales a 31/08: sin discrepancia, se publica", cu16)

    cargar(f("CU15"))

    def cu15():
        d = doc("PRO-CRC-2026-106")
        ps = pares("PRO-CRC-2026-106")
        inc = [p for p in ps if str(p.get("resultado", "")).startswith("incomparable_dias_habiles")]
        ds = discs(doc_a="PRO-CRC-2026-106")
        con_nota = [x for x in ds if "unidades distintas" in (x["motivo"] or "")]
        ok = bool(inc) and any(x["severidad"] == "alta" for x in con_nota) and d["estado"] == "pendiente_revision"
        return ok, (f"{resumen_doc(d)}; pares incomparables={len(inc)}; discrepancias="
                    f"{[(x['severidad'], x['doc_b'], x['motivo']) for x in con_nota] or sev('PRO-CRC-2026-106')}")
    caso("15", rel("31", "CU15"), "hábiles vs calendario no se convierten: par 'incomparable' al LLM; 20 calendario "
                                  "vs 5º hábil confirmado -> ALTA con nota 'unidades distintas'", cu15)

    # ---------------------------------------------------------------- B5-8 vigencia
    cargar(f("CU05"))

    def cu5():
        d, d02 = doc("RES-SINT-2026-101"), doc("RES-SINT-2023-009")
        m = md(d)
        ok = (d["reemplaza_a"] == "RES-SINT-2023-009" and m["fuente"].get("reemplaza_a") == "clausula_derogacion"
              and d02["estado"] == "reemplazada" and d02["reemplazada_por"] == "RES-SINT-2026-101")
        return ok, (f"{resumen_doc(d)} reemplaza_a={d['reemplaza_a']} (fuente {m['fuente'].get('reemplaza_a')}); "
                    f"02 estado={d02['estado']} por={d02['reemplazada_por']}")
    caso("5", rel("02", "CU05"), "cláusula 'Deróguese la RES-SINT-2023-009' sin ficha: reemplaza_a inferido "
                                 "(clausula_derogacion) y 02 queda reemplazada", cu5)

    cargar(f("CU06"))

    def cu6():
        d, d05 = doc("RES-SINT-2022-102"), doc("RES-SINT-2023-018")
        adv = md(d).get("advertencias") or []
        ok = (d05["estado"] == "vigente" and d05["reemplazada_por"] is None and d["requiere_confirmacion"] == 1
              and any("no se aplica el reemplazo" in a for a in adv))
        return ok, f"{resumen_doc(d)}; 05 estado={d05['estado']}; advertencias={adv}"
    caso("6", rel("05", "CU06"), "reemplaza_a apunta a norma posterior: no se aplica, advertencia, "
                                 "requiere_confirmacion=1", cu6)

    cargar(f("CU07"))

    def cu7():
        d = doc("RES-SINT-2026-103")
        ok = d["estado"] == "por_entrar_en_vigencia" and d["estado_inferido"] == 1 and d["publicado"] == 1
        return ok, f"{resumen_doc(d)} vigencia={d['fecha_vigencia']}"
    caso("7", rel("CU07"), "Estado '—' y vigencia 2027-01-01: estado inferido 'por_entrar_en_vigencia'", cu7)

    cargar(f("CU08"))

    def cu8():
        d = doc("POL-DIG-2024-104")
        ok = d["estado"] == "vigente" and d["estado_inferido"] == 1 and d["publicado"] == 1
        return ok, f"{resumen_doc(d)} vigencia={d['fecha_vigencia']}"
    caso("8", rel("CU08"), "Estado '—' y vigencia 2024-05-01: estado inferido 'vigente'", cu8)

    # ---------------------------------------------------------------- D. integridad / metadatos
    r20 = cargar(f("CU20"))

    def cu20():
        d = doc(r20["doc_id"])
        m = md(d)
        conf = json.loads(d["confianza_json"] or "{}")
        ok = m.get("origen") == "llm" and d["requiere_confirmacion"] == 1 and bool(conf)
        return ok, (f"doc_id={d['doc_id']} codigo={d['codigo']} {resumen_doc(d)}; origen={m.get('origen')} "
                    f"faltantes={m.get('faltantes')} baja_conf={m.get('baja_confianza')} confianza={conf}")
    caso("20", rel("CU20"), "sin ficha: metadatos por LLM con confianza; faltantes=null; requiere_confirmacion=1",
         cu20)

    cargar(f("CU21"))

    def cu21():
        d = doc("CI-2026-108")
        m = md(d)
        ok = d["fecha_vigencia"] is None and d["requiere_confirmacion"] == 1 and "fecha_vigencia" in m["faltantes"]
        return ok, (f"{resumen_doc(d)} fecha_vigencia={d['fecha_vigencia']} faltantes={m['faltantes']} "
                    f"fuente_vigencia={m['fuente'].get('fecha_vigencia')}")
    caso("21", rel("CU21"), "sin 'Fecha de vigencia': LLM no la inventa -> null, faltante, requiere_confirmacion=1",
         cu21)

    cargar(f("CU22"))

    def cu22():
        d = doc("CI-2026-109")
        adv = md(d).get("advertencias") or []
        ok = d["requiere_confirmacion"] == 1 and any("anterior a fecha_emision" in a for a in adv)
        return ok, f"{resumen_doc(d)}; advertencias={adv}"
    caso("22", rel("CU22"), "vigencia < emisión: advertencia y requiere_confirmacion=1", cu22)

    # ---------------------------------------------------------------- E. formatos
    for p in ("CU23", "CU24a", "CU24b"):
        cargar(f(p))

    def cu23():
        d = doc("POL-TEC-2026-110")
        ok = bool(d) and d["publicado"] == 1 and md(d).get("origen") == "frontmatter" and d["n_chunks"] == 3
        return ok, f"{resumen_doc(d)} origen={md(d).get('origen') if d else None}"
    caso("23", rel("CU23"), "DOCX con ficha: metadatos de la ficha (origen frontmatter), 3 chunks, publicado", cu23)

    def cu24():
        a, b = doc("PRO-ATE-2026-111"), doc("PRO-REC-2026-112")
        ok = all(x and x["publicado"] == 1 and md(x).get("origen") == "frontmatter" and x["n_chunks"] == 3
                 for x in (a, b))
        return ok, f"TXT: {resumen_doc(a)}; MD: {resumen_doc(b)}"
    caso("24", rel("CU24a", "CU24b"), "TXT con ficha y MD con YAML: metadatos declarados, 3 chunks, publicados", cu24)

    cargar(f("CU25"))

    def cu25():
        d = doc("POL-ETI-2026-113")
        n_idx = st.contar_prefijo("POL-ETI-2026-113")
        ok = d["estado_ingesta"] == "requiere_revision" and d["n_chunks"] == 0 and d["publicado"] == 0 and n_idx == 0
        return ok, f"{resumen_doc(d)} vectores={n_idx}; mensaje={d['mensaje_ingesta'][:90]}"
    caso("25", rel("CU25"), "sin 'Artículo N': requiere_revision, 0 chunks, no se indexa", cu25)

    r26 = cargar(f("CU26"))

    def cu26():
        d = doc(r26["doc_id"])
        ok = d["estado_ingesta"] == "requiere_revision" and d["n_chunks"] == 0 and d["publicado"] == 0
        return ok, f"doc_id={d['doc_id']} {resumen_doc(d)}; mensaje={d['mensaje_ingesta'][:90]}"
    caso("26", rel("CU26"), "PDF solo imagen: texto vacío -> requiere_revision, no se indexa, sin LLM", cu26)

    # ---------------------------------------------------------------- D17-19 duplicados (API)
    def cu17():
        pref = "RES-SINT-2024-001_"
        antes = st.contar_prefijo(pref)
        antes_sql = db.query("SELECT COUNT(*) n FROM chunks WHERE doc_id = 'RES-SINT-2024-001'")[0]["n"]
        r = subir_api(f("31"))
        time.sleep(5)
        despues = st.contar_prefijo(pref)
        despues_sql = db.query("SELECT COUNT(*) n FROM chunks WHERE doc_id = 'RES-SINT-2024-001'")[0]["n"]
        j = r.json()
        ok = r.status_code == 202 and j.get("duplicado") is True and antes == despues > 0 and antes_sql == despues_sql
        return ok, (f"HTTP {r.status_code} duplicado={j.get('duplicado')} doc_id={j.get('doc_id')}; vectores "
                    f"{antes}->{despues}; chunks {antes_sql}->{despues_sql}")
    caso("17", rel("31"), "POST del mismo archivo: 202 duplicado=true, no reindexa", cu17)

    def cu18():
        r = subir_api(f("CU18"))
        j = r.json()
        ok = r.status_code == 202 and j.get("duplicado") is True and j.get("doc_id") == "RES-SINT-2023-018"
        return ok, f"HTTP {r.status_code} duplicado={j.get('duplicado')} doc_id={j.get('doc_id')}: {j.get('mensaje')}"
    caso("18", rel("05", "CU18"), "mismo contenido con otro nombre: duplicado por sha256 (doc 05)", cu18)

    r19 = cargar(f("CU19"))

    def cu19():
        d, d05 = doc(r19["doc_id"]), doc("RES-SINT-2023-018")
        ch = db.query("SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY rowid LIMIT 1", (r19["doc_id"],))
        ok = (r19.get("duplicado") is False and r19["doc_id"] == "RES-SINT-2023-018_v1.0"
              and d["estado"] == "vigente" and d05["estado"] == "vigente")
        return ok, (f"doc_id={r19['doc_id']} {resumen_doc(d)} chunk={ch[0]['chunk_id'] if ch else None}; "
                    f"05 sigue estado={d05['estado']} publicado={d05['publicado']}; discrepancias: {sev(r19['doc_id'])}")
    caso("19", rel("05", "CU19"), "comportamiento del código: NO es duplicado (otro sha); nuevo doc_id "
                                  "RES-SINT-2023-018_v1.0 y ambas versiones quedan vigentes", cu19)

    # ---------------------------------------------------------------- E27-28 rechazos
    def cu27():
        out, ok = [], True
        for p in sorted(C.glob("CU27_*")):
            r = subir_api(p)
            out.append(f"{p.suffix} HTTP {r.status_code} {r.json().get('detail')}")
            ok &= r.status_code == 400
        filas = db.query("SELECT COUNT(*) n FROM documentos WHERE archivo LIKE '%CU27_%'")[0]["n"]
        return ok and filas == 0 and len(out) == 2, "; ".join(out) + f"; filas en documentos={filas}"
    caso("27", rel("CU27_foto", "CU27_tabla"), "xlsx/jpg: HTTP 400 'Formato no soportado', nada se registra", cu27)

    def cu28():
        r = subir_api(f("CU28"))
        try:
            ingerir(f("CU28"))
            directo = "ingerir() lo procesó"
        except ArchivoRechazado as e:
            directo = f"ArchivoRechazado: {e}"
        ok = r.status_code == 400 and directo.startswith("ArchivoRechazado")
        return ok, f"API HTTP {r.status_code} {r.json().get('detail')}; ingesta directa: {directo}"
    caso("28", rel("CU28"), "nombre con NO_SUBIR: HTTP 400 y ArchivoRechazado en la ingesta directa", cu28)

    # ---------------------------------------------------------------- F. decisión humana
    cargar(f("12"))
    cargar(f("13"))

    def decision(n: str, doc_id: str, dec: str, esperado_fn):
        def fn():
            d0 = doc(doc_id)
            if d0["estado"] != "pendiente_revision":
                return False, f"precondición: {doc_id} no quedó pendiente_revision ({resumen_doc(d0)})"
            r = decidir_api(doc_id, dec)
            d = doc(doc_id)
            abiertas = db.query("SELECT COUNT(*) n FROM discrepancias WHERE doc_a = ? AND severidad = 'alta' AND "
                                "COALESCE(estado,'abierta') = 'abierta'", (doc_id,))[0]["n"]
            idx = estados_indice(doc_id, esperado_fn["indice"], 4)
            t = traza(doc_id, "decision_usuario")
            ok = (r.status_code == 200 and d["estado"] == esperado_fn["estado"] and d["publicado"] == esperado_fn["pub"]
                  and (abiertas > 0) == esperado_fn["altas_abiertas"] and idx == {esperado_fn["indice"]} and t)
            return ok, (f"HTTP {r.status_code} {r.json()}; {resumen_doc(d)}; ALTA abiertas={abiertas}; índice={idx}; "
                        f"traza decision_usuario={len(t)}")
        return fn

    caso("29", rel("09"), "aprobar_publicar sobre 09: vigente, publicado=1, ALTAs resueltas, índice vigente",
         decision("29", "MAN-CR-2025-004", "aprobar_publicar",
                  {"estado": "vigente", "pub": 1, "altas_abiertas": False, "indice": "vigente"}))
    caso("30", rel("12"), "publicar_igual (sobre 12, otra ALTA pendiente): vigente, publicado=1, ALTA sigue abierta",
         decision("30", "PRO-REP-2024-007", "publicar_igual",
                  {"estado": "vigente", "pub": 1, "altas_abiertas": True, "indice": "vigente"}))
    caso("31", rel("13"), "rechazar (sobre 13, otra ALTA pendiente): rechazada, publicado=0, discrepancias "
                          "resueltas, índice 'rechazada'",
         decision("31", "POL-DOC-2023-008", "rechazar",
                  {"estado": "rechazada", "pub": 0, "altas_abiertas": False, "indice": "rechazada"}))

    # ---------------------------------------------------------------- G. áreas de aplicación
    cargar(f("CU32"))

    def cu32():
        d = doc("POL-PUB-2026-132")
        esperado = ["Marketing", "Call Center", "Legal"]
        idx = areas_indice("POL-PUB-2026-132", esperado)
        tr = [t.get("areas") for t in traza("POL-PUB-2026-132", "metadatos")]
        ok = (de_json(d["areas_aplica"]) == esperado and d["areas_fuente"] == "ficha" and d["areas_confianza"] == 1.0
              and d["areas_requiere_confirmacion"] == 0 and idx == [esperado] and bool(tr) and bool(tr[-1])
              and tr[-1].get("areas_fuente") == "ficha")
        return ok, f"{areas_doc(d)}; índice={idx}; traza metadatos.areas={tr[-1] if tr else None}"
    caso("32", rel("CU32"), "fila 'Áreas de aplicación' de la ficha: fuente ficha, lista exacta, requiere 0, "
                            "metadato areas_aplica en el índice", cu32)

    cargar(f("CU33"))

    def cu33():
        d = doc("PRO-COB-2026-133")
        esperado = ["Cobranzas", "Legal", "RRHH"]
        idx = areas_indice("PRO-COB-2026-133", esperado)
        ok = (de_json(d["areas_aplica"]) == esperado and d["areas_fuente"] == "frontmatter"
              and d["areas_requiere_confirmacion"] == 0 and idx == [esperado])
        return ok, f"{areas_doc(d)}; índice={idx}"
    caso("33", rel("CU33"), "frontmatter areas_aplica 'Cobranzas, legal, RR.HH.' -> [Cobranzas, Legal, RRHH], "
                            "fuente frontmatter, requiere 0", cu33)

    def cu34():
        d = doc("RES-SINT-2023-009")
        lista = de_json(d["areas_aplica"])
        tr = [t.get("areas") for t in traza("RES-SINT-2023-009", "metadatos")]
        ok = (d["areas_fuente"] == "llm" and d["areas_requiere_confirmacion"] == 1 and all(a in AREAS for a in lista)
              and bool(tr) and bool(tr[-1]) and tr[-1].get("areas_fuente") == "llm")
        return ok, f"{areas_doc(d)}; llm={(tr[-1] or {}).get('llm') if tr else None}"
    caso("34", rel("02"), "sin áreas declaradas: UNA llamada al LLM, fuente llm, requiere 1, todas en la lista "
                          "(requiere_confirmacion del documento no cambia)", cu34)

    def cu35():
        doc_id = "RES-SINT-2024-001"
        nuevas = ["Créditos", "Riesgos"]
        esperado = [a for a in AREAS if a in nuevas]
        est0 = doc(doc_id)["estado"]
        mala = api().put(f"/api/documentos/{doc_id}/areas", json={"areas": ["Ventas"], "usuario": "verificacion"})
        r = api().put(f"/api/documentos/{doc_id}/areas", json={"areas": nuevas, "usuario": "verificacion"})
        d = doc(doc_id)
        idx = areas_indice(doc_id, esperado)
        est_idx = estados_indice(doc_id, est0)
        t = traza(doc_id, "edicion_areas")
        ent = db.query("SELECT entrada FROM trazas_ingesta WHERE doc_id = ? AND paso = 'edicion_areas' "
                       "ORDER BY id DESC LIMIT 1", (doc_id,))
        det = api().get(f"/api/documentos/{doc_id}").json()
        ok = (mala.status_code == 400 and r.status_code == 200 and r.json().get("areas_aplica") == esperado
              and d["areas_fuente"] == "manual" and d["areas_confianza"] == 1.0 and d["areas_requiere_confirmacion"] == 0
              and idx == [esperado] and est_idx == {est0} and bool(t) and t[-1]["despues"]["areas_aplica"] == esperado
              and bool(ent) and "antes" in json.loads(ent[0]["entrada"]) and det.get("areas_aplica") == esperado)
        return ok, (f"PUT inválida HTTP {mala.status_code}; PUT HTTP {r.status_code} {r.json()}; {areas_doc(d)}; "
                    f"índice={idx} estado índice={est_idx}; traza edicion_areas={len(t)}")
    caso("35", rel("31"), "PUT /areas: 400 si no está en la lista; fuente manual, Pinecone actualizado con el mismo "
                          "estado, paso edicion_areas con antes/después", cu35)

    def cu36():
        pregunta = "¿Cuál es el límite máximo de la relación cuota-ingreso para un crédito de consumo?"
        r = api().post("/api/chat", data={"session_id": "cu36", "mensaje": pregunta},
                       headers={"X-Usuario": "marketing"})
        j = r.json()
        citas = j.get("citas") or []
        ajenas = [c for c in citas if c.get("areas_aplica") and "Marketing" not in c["areas_aplica"]]
        coherente = all((c.get("nota_area") is not None)
                        == (bool(c.get("areas_aplica")) and "Marketing" not in c["areas_aplica"]) for c in citas)
        cons = db.query("SELECT area_usuario FROM consultas WHERE run_id = ?", (j.get("run_id"),))
        hist = api().get("/api/chat/cu36", headers={"X-Usuario": "marketing"}).json()
        h_ag = [m for m in hist if m["rol"] == "agente"]
        pay = (h_ag[-1]["payload"] or {}) if h_ag else {}
        nota = "Esta norma no es de tu área (Marketing), pero te aplica."
        ok = (r.status_code == 200 and j.get("area_usuario") == "Marketing" and bool(ajenas) and coherente
              and all(c["nota_area"] == nota for c in ajenas)
              and bool(cons) and cons[0]["area_usuario"] == "Marketing"
              and pay.get("area_usuario") == "Marketing"
              and any(c.get("nota_area") for c in pay.get("citas") or []))
        return ok, (f"HTTP {r.status_code} area_usuario={j.get('area_usuario')} confianza={j.get('confianza')}; citas="
                    f"{[(c.get('doc_id'), c.get('areas_aplica'), bool(c.get('nota_area'))) for c in citas]}; "
                    f"consultas.area_usuario={cons[0]['area_usuario'] if cons else None}; "
                    f"historial enriquecido={pay.get('area_usuario')}")
    caso("36", rel("31"), "chat de usuario marketing sobre norma sin Marketing: nota_area en la cita, area_usuario "
                          "en respuesta, consulta e historial", cu36)

    def cu37():
        ejemplos = api().get("/api/chat/ejemplos").json()
        texto = ejemplos[0]["texto"] if ejemplos else ""
        r = api().post("/api/chat", data={"session_id": "cu37", "mensaje": texto}, headers={"X-Usuario": "marketing"})
        j = r.json()
        dic = j.get("dictamen") or {}
        evs = [e for e in dic.get("evaluaciones") or [] if e.get("doc_id")]
        ok = (r.status_code == 200 and j.get("tipo") == "caso" and bool(dic.get("areas_a_coordinar"))
              and all("areas_aplica" in e and "nota_area" in e for e in evs)
              and set(dic["areas_a_coordinar"]) <= set(AREAS))
        return ok, (f"HTTP {r.status_code} tipo={j.get('tipo')} conclusion={dic.get('conclusion')} "
                    f"areas_a_coordinar={dic.get('areas_a_coordinar')} evaluaciones={len(evs)}")
    caso("37", "../corpus/casos (ejemplo 1)", "caso: dictamen.areas_a_coordinar = unión de áreas de las normas "
                                              "citadas", cu37)


def main() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    ns = get_settings().pinecone_namespace
    if ns == "demo":
        sys.exit("PINECONE_NAMESPACE=demo es el del VPS: corre la verificación en 'dev'")
    print(f"Namespace={ns}  backend={get_store().backend}  db={db.db_file()}")
    t0 = time.time()
    escenario_1()
    escenario_2()

    RES.sort(key=lambda r: (int("".join(c for c in r["caso"] if c.isdigit()) or 99), r["caso"]))
    print("\n" + "=" * 150)
    print(f"{'CASO':<5}| {'archivo(s)':<46}| {'esperado':<44}| {'resultado':<12}| obtenido")
    print("-" * 150)
    for r in RES:
        print(f"CU{r['caso']:<3}| {r['archivos'][:45]:<46}| {r['esperado'][:43]:<44}| {r['resultado']:<12}| "
              f"{r['obtenido'][:260]}")
    print("-" * 150)
    cuenta = {k: sum(1 for r in RES if r["resultado"] == k) for k in ("PASS", "FAIL", "NO SOPORTADO")}
    print(f"PASS={cuenta['PASS']} FAIL={cuenta['FAIL']} NO SOPORTADO={cuenta['NO SOPORTADO']}  "
          f"Tiempo total: {time.time() - t0:.0f}s")
    print("\nDiscrepancias del escenario 2:")
    for x in db.query("SELECT * FROM discrepancias ORDER BY id"):
        print(f"  #{x['id']} [{x['severidad']}/{x['tipo']}/{x['estado']}] {x['tema']}: {x['doc_a']} art {x['art_a']} "
              f"vs {x['doc_b']} art {x['art_b']} prevalece={x['prevalece']}")
    sys.exit(1 if cuenta["FAIL"] else 0)


if __name__ == "__main__":
    main()
