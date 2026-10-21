"""Pruebas de aceptación del Agente 1 (ingesta y validación) contra el código, en el namespace del .env.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/aceptacion_agente1.py

ATENCIÓN: hace --reset (borra documentos/chunks/afirmaciones/discrepancias/trazas_ingesta de la SQLite local y el
namespace actual del índice). Se niega a correr si PINECONE_NAMESPACE es 'demo' (el del VPS).
Orden: base -> 01 -> RES-SINT-2024-001 (si existe) -> 08 -> 09 -> 12 -> 18, y luego las verificaciones.
Las pruebas que necesitan RES-SINT-2024-001 (norma_credito_sintetica_v2024.md) dan SKIP si falta el archivo.
Al terminar, deja cargado lo que cargó (vuelve a sembrar con seed_corpus.py --reset --normas-demo --hasta-base).
"""
from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.normas import db  # noqa: E402
from app.normas.ingesta import ArchivoRechazado, ingerir, reindexar  # noqa: E402
from app.normas.validacion import cita_verificada  # noqa: E402
from app.normas.vector import get_store  # noqa: E402
from scripts.seed_corpus import BASE, COD_2024, archivos_demo, cargar, reset  # noqa: E402

RESULTADOS: list[tuple[str, str, str]] = []


def registrar(nombre: str, estado: str, detalle: str) -> None:
    RESULTADOS.append((nombre, estado, detalle))
    print(f"   -> [{estado}] {nombre}: {detalle}")


def discs(**f) -> list[dict]:
    where = " AND ".join(f"{k} = ?" for k in f) or "1=1"
    return db.query(f"SELECT * FROM discrepancias WHERE {where} ORDER BY id", tuple(f.values()))


def doc(doc_id: str) -> dict | None:
    r = db.query("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,))
    return r[0] if r else None


def estados_indice(doc_id: str, esperado: str, intentos: int = 6) -> set:
    """Estados de los chunks de doc_id en el índice (reintenta por la consistencia eventual de Pinecone)."""
    est: set = set()
    for _ in range(intentos):
        hits = get_store().search("norma artículo", top_k=50, filter={"doc_id": doc_id})
        est = {h.metadata.get("estado") for h in hits}
        if est == {esperado}:
            return est
        time.sleep(2)
    return est


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ns = get_settings().pinecone_namespace
    if ns == "demo":
        sys.exit("PINECONE_NAMESPACE=demo es el del VPS: corre la aceptación en 'dev'")
    t_total = time.time()
    a = archivos_demo()
    hay_2024 = COD_2024 in a
    print(f"Namespace={ns}  backend={get_store().backend}  RES-SINT-2024-001 presente={hay_2024}\n")
    reset()

    # ---------------------------------------------------------------- carga
    print("\n== Carga base ==")
    for k in BASE:
        if k in a:
            cargar(a[k])
    print("\n== Prueba 1: 2019-014 y luego 2024-001 ==")
    cargar(a["01"])
    if hay_2024:
        r24 = cargar(a[COD_2024])
        d19 = doc("RES-SINT-2019-014")
        est = estados_indice("RES-SINT-2019-014", "reemplazada")
        ok = (d19["estado"] == "reemplazada" and d19["reemplazada_por"] == r24["doc_id"] and est == {"reemplazada"})
        registrar("1 2019 reemplazada por 2024-001", "PASS" if ok else "FAIL",
                  f"SQLite estado={d19['estado']} reemplazada_por={d19['reemplazada_por']}; índice={est}")
    else:
        registrar("1 2019 reemplazada por 2024-001", "SKIP", f"falta {COD_2024} en normas_demo/")

    print("\n== Extra: 07 -> 08 ==")
    cargar(a["08"])
    d07 = doc("POL-RC-2022-001")
    est = estados_indice("POL-RC-2022-001", "reemplazada")
    ok = d07["estado"] == "reemplazada" and d07["reemplazada_por"] == "POL-RC-2025-002" and est == {"reemplazada"}
    registrar("extra POL-RC-2022-001 reemplazada por POL-RC-2025-002", "PASS" if ok else "FAIL",
              f"SQLite estado={d07['estado']} reemplazada_por={d07['reemplazada_por']}; índice={est}")

    print("\n== Prueba 2: MAN-CR-2025-004 ==")
    cargar(a["09"])
    if hay_2024:
        altas = discs(doc_a="MAN-CR-2025-004", doc_b="RES-SINT-2024-001", severidad="alta")
        temas = {x["tema"] for x in altas}
        d = doc("MAN-CR-2025-004")
        ok = {"cuota_ingreso", "refinanciacion_plazo"} <= temas and d["estado"] == "pendiente_revision" \
            and d["publicado"] == 0
        registrar("2 MAN-CR-2025-004 ALTAS cuota_ingreso y refinanciacion_plazo", "PASS" if ok else "FAIL",
                  f"temas ALTA vs 2024-001={sorted(temas)}; estado={d['estado']} publicado={d['publicado']}; "
                  f"índice={estados_indice('MAN-CR-2025-004', 'pendiente_revision', 3)}")
    else:
        registrar("2 MAN-CR-2025-004 ALTAS cuota_ingreso y refinanciacion_plazo", "SKIP",
                  f"falta {COD_2024}")

    print("\n== Prueba 3: PRO-REP-2024-007 ==")
    cargar(a["12"])
    if hay_2024:
        altas = discs(doc_a="PRO-REP-2024-007", doc_b="RES-SINT-2024-001", severidad="alta",
                      tema="reporte_central_riesgos")
        registrar("3 PRO-REP-2024-007 ALTA décimo vs quinto día hábil", "PASS" if altas else "FAIL",
                  f"{len(altas)} ALTA: " + "; ".join(f"{x['cita_a']} | {x['cita_b']}" for x in altas))
    else:
        registrar("3 PRO-REP-2024-007 ALTA décimo vs quinto día hábil", "SKIP", f"falta {COD_2024}")

    print("\n== Prueba 4: CI-2025-013 ==")
    cargar(a["18"])
    medias = discs(doc_a="CI-2025-013", severidad="media", tema="facultad_aprobacion")
    contra = {x["doc_b"] for x in medias}
    ok = {"POL-RC-2025-002", "CI-2025-012"} <= contra
    registrar("4 CI-2025-013 MEDIA vs POL-RC-2025-002 y CI-2025-012", "PASS" if ok else "FAIL",
              f"MEDIA contra {sorted(contra)}; " + "; ".join(f"{x['cita_a']} | {x['cita_b']}" for x in medias))

    print("\n== Prueba 5: MAN-HIP-2024-006 ==")
    graves = [x for x in db.query("SELECT * FROM discrepancias WHERE (doc_a = 'MAN-HIP-2024-006' OR "
                                  "doc_b = 'MAN-HIP-2024-006') AND severidad IN ('alta', 'media')")]
    info = discs(doc_a="MAN-HIP-2024-006", severidad="informativa")
    registrar("5 MAN-HIP-2024-006 sin ALTA/MEDIA (más estricto)", "PASS" if not graves else "FAIL",
              f"ALTA/MEDIA={len(graves)}; informativas={[(x['tema'], x['doc_b']) for x in info]}")

    print("\n== Prueba 6: re-subir no duplica ==")
    st = get_store()
    pref = "MAN-CR-2025-004_"
    antes_idx = st.contar_prefijo(pref)
    antes_sql = db.query("SELECT COUNT(*) n FROM chunks WHERE doc_id = 'MAN-CR-2025-004'")[0]["n"]
    tmp = Path(tempfile.mkdtemp()) / "copia_con_otro_nombre.md"
    shutil.copy(a["09"], tmp)
    r = ingerir(tmp, usuario="aceptacion", canal="script")
    reindexar("MAN-CR-2025-004")  # además: un re-upsert explícito tampoco duplica (ids determinísticos)
    time.sleep(8)
    despues_idx = st.contar_prefijo(pref)
    despues_sql = db.query("SELECT COUNT(*) n FROM chunks WHERE doc_id = 'MAN-CR-2025-004'")[0]["n"]
    ok = r.get("duplicado") is True and antes_idx == despues_idx and antes_idx > 0 and antes_sql == despues_sql
    registrar("6 re-subir no duplica vectores", "PASS" if ok else "FAIL",
              f"duplicado={r.get('duplicado')} vectores '{pref}*' antes={antes_idx} después={despues_idx}; "
              f"chunks SQLite {antes_sql}->{despues_sql}")

    print("\n== Prueba 7: citas verificables ==")
    malas, total = [], 0
    for x in db.query("SELECT * FROM discrepancias"):
        total += 1
        ta = (db.query("SELECT texto FROM chunks WHERE chunk_id = ?", (x["chunk_a"],)) or [{"texto": ""}])[0]["texto"]
        tb = (db.query("SELECT texto FROM chunks WHERE chunk_id = ?", (x["chunk_b"],)) or [{"texto": ""}])[0]["texto"]
        if not (cita_verificada(x["cita_a"], ta) and cita_verificada(x["cita_b"], tb)):
            malas.append(x["id"])
    registrar("7 toda cita existe literalmente en su chunk", "PASS" if total and not malas else
              ("FAIL" if malas else "SKIP"), f"{total} discrepancias, {len(malas)} con cita no literal {malas}")

    try:
        ingerir(ROOT / "normas_demo" / "00_GUIA_DEMO_NO_SUBIR.md")
        registrar("extra guía rechazada", "FAIL", "la guía se procesó")
    except ArchivoRechazado as e:
        registrar("extra guía rechazada", "PASS", str(e))

    # ---------------------------------------------------------------- resumen
    print("\n" + "=" * 100)
    print(f"{'Prueba':<62} {'Resultado':<9} Detalle")
    print("-" * 100)
    for n, e, dt in RESULTADOS:
        print(f"{n:<62} {e:<9} {dt[:160]}")
    print("-" * 100)
    cuenta = {k: sum(1 for _, e, _ in RESULTADOS if e == k) for k in ("PASS", "FAIL", "SKIP")}
    print(f"PASS={cuenta['PASS']} FAIL={cuenta['FAIL']} SKIP={cuenta['SKIP']}   Tiempo total: "
          f"{time.time() - t_total:.1f}s")
    print("\nDiscrepancias registradas:")
    for x in db.query("SELECT * FROM discrepancias ORDER BY id"):
        print(f"  #{x['id']} [{x['severidad']}/{x['tipo']}/{x['estado']}] {x['tema']}: {x['doc_a']} art {x['art_a']} "
              f"vs {x['doc_b']} art {x['art_b']} prevalece={x['prevalece']}")
    sys.exit(1 if cuenta["FAIL"] else 0)


if __name__ == "__main__":
    main()
