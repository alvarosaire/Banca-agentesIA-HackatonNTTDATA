"""Carga el corpus de demo y espera cada validación (Agente 1).

    .venv/Scripts/python scripts/seed_corpus.py --reset --normas-demo --hasta-base   # base de la demo
    .venv/Scripts/python scripts/seed_corpus.py --reset --normas-demo                # todo normas_demo/
    .venv/Scripts/python scripts/seed_corpus.py --reset                              # corpus/ antiguo (SINT-*)

Orden de normas_demo (según la guía del equipo; la guía 00_GUIA*/NO_SUBIR nunca se carga):
  1. base: 02 03 04 05 07 10 11 17 19 20 21 22 23 24 25 26 29 30
  2. 01 (RES-SINT-2019-014) y después RES-SINT-2024-001 (si está en normas_demo/): la 2019 queda reemplazada
  3. 08 (POL-RC-2025-002 reemplaza a POL-RC-2022-001)
  4. contradicciones en vivo: 09 12 13 14 15 16 18 06 27 28   (se omiten con --hasta-base)
--reset borra documentos, chunks, afirmaciones, discrepancias, trazas_ingesta y el namespace ACTUAL del índice
(PINECONE_NAMESPACE del .env; nunca toca otro namespace).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.normas import db  # noqa: E402
from app.normas.ingesta import es_archivo_prohibido, ingerir  # noqa: E402
from app.normas.parsing import separar_frontmatter  # noqa: E402
from app.normas.vector import get_store  # noqa: E402

DEMO = ROOT / "normas_demo"
ORDEN_ANTIGUO = ["SINT-01.txt", "SINT-02.txt", "SBS-EJ-01.txt", "SBS-EJ-02.txt"]
BASE = ["02", "03", "04", "05", "07", "10", "11", "17", "19", "20", "21", "22", "23", "24", "25", "26", "29", "30"]
EN_VIVO = ["09", "12", "13", "14", "15", "16", "18", "06", "27", "28"]
COD_2024 = "RES-SINT-2024-001"


def codigo_de(path: Path) -> str | None:
    if path.suffix.lower() == ".pdf":
        from app.normas.parsing import parsear
        texto = "\n".join(t for _, t in parsear(path)[0])
    else:
        texto = path.read_text(encoding="utf-8-sig", errors="replace")
    fm, _ = separar_frontmatter(texto)
    return (fm.get("codigo") or "").strip() or None


def archivos_demo() -> dict[str, Path]:
    """prefijo ('02') o código -> archivo. Excluye la guía."""
    out: dict[str, Path] = {}
    # .md primero; un .pdf solo cubre lo que no tenga su .md (p. ej. RES-SINT-2024-001)
    for p in sorted(DEMO.glob("*.md")) + sorted(DEMO.glob("*.pdf")):
        if es_archivo_prohibido(p.name):
            continue
        pref = p.name.split("_", 1)[0]
        cod = codigo_de(p)
        if (pref.isdigit() and pref in out) or (cod and cod in out):
            continue
        if pref.isdigit():
            out[pref] = p
        if cod:
            out[cod] = p
    return out


def orden_demo(hasta_base: bool) -> list[Path]:
    a = archivos_demo()
    orden = [a[k] for k in BASE if k in a]
    if "01" in a:
        orden.append(a["01"])
    if COD_2024 in a:
        orden.append(a[COD_2024])
    else:
        print(f"AVISO: falta {COD_2024} (norma_credito_sintetica_v2024.md) en normas_demo/: se omite")
    if "08" in a:
        orden.append(a["08"])
    if not hasta_base:
        orden += [a[k] for k in EN_VIVO if k in a]
    vistos, out = set(), []
    for p in orden:
        if p not in vistos:
            vistos.add(p)
            out.append(p)
    return out


def reset() -> None:
    store = get_store()
    store.delete_all()
    with db.connect() as con:
        for t in ("discrepancias", "afirmaciones", "trazas_ingesta", "chunks_fts", "chunks", "documentos"):
            con.execute(f"DELETE FROM {t}")
        con.execute("DELETE FROM sqlite_sequence WHERE name IN ('discrepancias', 'afirmaciones', 'trazas_ingesta')")
    print(f"Reset: namespace '{get_settings().pinecone_namespace}' ({store.backend}) y tablas del Agente 1 borrados")


def cargar(path: Path, metadatos: dict | None = None, validar: bool = True) -> dict:
    t0 = time.time()
    res = ingerir(path, metadatos=metadatos, validar=validar, usuario="seed", canal="script")
    n = db.query("SELECT severidad, COUNT(*) n FROM discrepancias WHERE doc_a = ? GROUP BY severidad",
                 (res["doc_id"],))
    sev = ", ".join(f"{r['severidad']}={r['n']}" for r in n) or "sin discrepancias"
    print(f"  {path.name:<34} -> {res['doc_id']:<20} {time.time() - t0:5.1f}s  estado={res.get('estado')} "
          f"publicado={res.get('publicado')} ingesta={res.get('estado_ingesta')}"
          f"{' DUPLICADO' if res.get('duplicado') else ''}  [{sev}]")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="borra namespace actual y tablas del Agente 1")
    ap.add_argument("--normas-demo", action="store_true", help="carga normas_demo/*.md en el orden de la guía")
    ap.add_argument("--hasta-base", action="store_true", help="con --normas-demo: omite las contradicciones en vivo")
    ap.add_argument("--all", action="store_true", help="corpus antiguo: incluye SINT-03")
    ap.add_argument("--sin-validar", action="store_true", help="no compara contra el corpus")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = get_store()
    print(f"Vector store: {store.backend}  namespace={get_settings().pinecone_namespace}  db={db.db_file()}")
    if args.reset:
        reset()
    t0 = time.time()
    if args.normas_demo:
        for p in orden_demo(args.hasta_base):
            cargar(p, validar=not args.sin_validar)
    else:
        metas = json.loads((ROOT / "corpus" / "metadatos.json").read_text(encoding="utf-8"))
        for nombre in ORDEN_ANTIGUO + (["SINT-03.txt"] if args.all else []):
            cargar(ROOT / "corpus" / nombre, metadatos=metas[nombre], validar=not args.sin_validar)
    print(f"\nTiempo total: {time.time() - t0:.1f}s")

    print("\nDocumentos:")
    for d in db.query("SELECT doc_id, version, estado, publicado, tipo_fuente, estado_ingesta, n_chunks, "
                      "reemplazada_por, nombre_norma FROM documentos ORDER BY created_at, rowid"):
        print(f"  {d['doc_id']:<20} v{d['version']:<4} {str(d['estado']):<20} pub={d['publicado']} "
              f"{str(d['tipo_fuente']):<8} chunks={d['n_chunks']:<3} "
              f"{('-> ' + d['reemplazada_por']) if d['reemplazada_por'] else ''}  {d['nombre_norma']}")
    print("\nDiscrepancias:")
    for x in db.query("SELECT * FROM discrepancias ORDER BY id"):
        print(f"  #{x['id']} [{x['severidad']}/{x['tipo']}] {x['tema']}: {x['doc_a']} art {x['art_a']} vs "
              f"{x['doc_b']} art {x['art_b']}  prevalece={x['prevalece']}")
        print(f"      a: {x['cita_a']}\n      b: {x['cita_b']}")


if __name__ == "__main__":
    main()
