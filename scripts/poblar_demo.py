"""Genera interacciones REALES contra la API para poblar los KPIs (nada se inventa: todo lo registran los agentes).

Uso:
    .venv/Scripts/python scripts/poblar_demo.py [--base http://127.0.0.1:8000] [--reset-consultas] [--n 8]

--reset-consultas borra (en la SQLite local de este equipo) consultas, brechas, chat_mensajes y las trazas de
los agentes consultas/analista_casos. NO borra documentos ni discrepancias.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TIMEOUT = 150

PREGUNTAS = [
    # cubiertas por el corpus
    "¿Cuál es el límite máximo de endeudamiento para un crédito de consumo?",
    "¿Qué scoring mínimo se exige para aprobar un crédito de consumo?",
    "¿Cuál es el plazo máximo de un crédito de consumo?",
    "¿Qué garantías se requieren para un crédito de consumo?",
    "¿Quién puede aprobar un crédito de consumo de S/ 50,000?",
    "¿Cuál es la autonomía de aprobación del gerente de agencia?",
    "¿En qué plazo se debe responder un reclamo de un cliente?",
    "¿Qué debe hacer el banco ante un evento de riesgo operacional?",
    "¿Cómo se calcula el ratio de endeudamiento del solicitante?",
    "¿Qué requisitos debe cumplir el solicitante de un crédito de consumo?",
    "¿Qué edad mínima y máxima se exige para otorgar un crédito?",
    "¿Se puede aprobar un crédito si el cliente tiene deudas vencidas?",
    "¿Cuántas cuotas como máximo puede tener un préstamo personal?",
    "¿Qué información de tasas se debe entregar al cliente antes de contratar?",
    "¿Qué nivel aprueba excepciones a la política de crédito?",
    "¿Cuál es el procedimiento para registrar un reclamo?",
    "¿Qué controles de riesgo operacional deben reportarse?",
    "¿Qué documentos sustentan el ingreso del cliente?",
    "¿Cuál es el monto máximo que puede aprobar un comité de créditos?",
    "¿Qué pasa si el scoring del cliente está por debajo del mínimo?",
    # probablemente sin cobertura (para detectar brechas)
    "¿Puede el banco aceptar criptoactivos como garantía?",
    "¿Cuáles son las condiciones para un contrato de leasing vehicular?",
    "¿Qué seguros de desgravamen son obligatorios en un crédito?",
    "¿Cómo se constituye un fideicomiso de garantía?",
    "¿Cuál es la política para créditos a no residentes?",
    "¿Qué requisitos hay para otorgar factoring a una pyme?",
    "¿Se permite financiar la compra de criptomonedas con tarjeta?",
    "¿Cuál es la política de cobranza judicial de la cartera castigada?",
    "¿Qué límites aplican a operaciones con derivados financieros?",
    "¿Cómo se evalúa un crédito agrícola con garantía de cosecha?",
]

SEGUIMIENTOS = [
    "¿Y si el ingreso neto mensual fuera S/ 9,000?",
    "¿Y si el scoring fuera 700?",
    "¿Y si el monto solicitado fuera S/ 20,000?",
    "¿Qué pasaría si lo aprueba el gerente regional?",
]

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def reset_consultas() -> None:
    from app.normas import casos, db
    casos.ensure_schema()
    with db.connect() as con:
        for sql in ("DELETE FROM consultas", "DELETE FROM brechas", "DELETE FROM chat_mensajes",
                    "DELETE FROM trazas WHERE agente IN ('consultas', 'analista_casos')"):
            n = con.execute(sql).rowcount
            log(f"  {sql}: {n} filas")


def asegurar_sint03(base: str) -> None:
    docs = requests.get(f"{base}/api/documentos", timeout=30).json()
    if any(d["doc_id"] == "SINT-03" for d in docs):
        log("SINT-03 ya está cargado")
        return
    path = ROOT / "corpus" / "SINT-03.txt"
    log("Subiendo corpus/SINT-03.txt ...")
    with open(path, "rb") as f:
        r = requests.post(f"{base}/api/documentos", files={"archivo": (path.name, f, "text/plain")}, timeout=TIMEOUT)
    r.raise_for_status()
    info = r.json()
    log(f"  {info}")
    run_id = info.get("run_id")
    t0 = time.time()
    while time.time() - t0 < 300:  # esperar indexado + validación
        d = requests.get(f"{base}/api/documentos/{info['doc_id']}", timeout=30).json()
        run_id = run_id or d.get("run_id")
        estado_val = None
        if run_id:
            t = requests.get(f"{base}/api/trazas/{run_id}", timeout=30)
            estado_val = t.json().get("estado") if t.ok else None
        if d.get("estado_ingesta") in ("requiere_revision", "error") or estado_val in ("completed", "failed"):
            log(f"  ingesta={d.get('estado_ingesta')} validación={estado_val} ({time.time() - t0:.0f}s)")
            return
        time.sleep(3)
    log("  aviso: la validación de SINT-03 no terminó en 300 s; se continúa")


def chat(base: str, session_id: str, mensaje: str) -> dict:
    r = requests.post(f"{base}/api/chat", data={"session_id": session_id, "mensaje": mensaje}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--reset-consultas", action="store_true")
    ap.add_argument("--n", type=int, default=0, help="limita el número de preguntas sueltas (0 = todas)")
    ap.add_argument("--hilos", type=int, default=3)
    ap.add_argument("--sin-sint03", action="store_true",
                    help="no sube SINT-03 (para dejarlo como carga en vivo durante la demo)")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    t_ini = time.time()

    if a.reset_consultas:
        log("Reset de consultas/trazas de agentes de consulta:")
        reset_consultas()
    if not a.sin_sint03:
        asegurar_sint03(base)

    preguntas = PREGUNTAS[: a.n] if a.n else PREGUNTAS
    if a.n:  # subconjunto: mezclar cubiertas y sin cobertura
        preguntas = PREGUNTAS[: max(1, a.n - a.n // 3)] + PREGUNTAS[20: 20 + a.n // 3]
    ejemplos = requests.get(f"{base}/api/chat/ejemplos", timeout=30).json()
    if a.n:
        ejemplos = ejemplos[:1]
    seguimientos = SEGUIMIENTOS[:1] if a.n else SEGUIMIENTOS

    ok, fallos = [], []
    tareas = [("consulta", f"demo-{uuid.uuid4().hex[:6]}", p) for p in preguntas]
    total = len(tareas) + len(ejemplos) + len(seguimientos)
    hechos = [0]

    def ejecutar(tipo: str, sid: str, msg: str) -> None:
        t0 = time.time()
        try:
            r = chat(base, sid, msg)
            ok.append(r)
            hechos[0] += 1
            log(f"[{hechos[0]}/{total}] ok {tipo:<11} {r.get('tipo'):<8} conf={r.get('confianza'):<12} "
                f"citas={len(r.get('citas') or [])} {time.time() - t0:5.1f}s  {msg[:60]}")
        except Exception as e:
            fallos.append((msg, str(e)))
            hechos[0] += 1
            log(f"[{hechos[0]}/{total}] FALLO {tipo} {msg[:60]}: {e}")

    def flujo_caso(i: int, ej: dict) -> None:
        sid = f"demo-caso-{i}-{uuid.uuid4().hex[:6]}"
        ejecutar("caso", sid, ej["texto"])
        if i == 0:  # seguimientos en la misma sesión del primer caso (secuenciales)
            for s in seguimientos:
                ejecutar("seguimiento", sid, s)

    with ThreadPoolExecutor(max_workers=a.hilos) as pool:
        futs = [pool.submit(flujo_caso, i, ej) for i, ej in enumerate(ejemplos)]
        futs += [pool.submit(ejecutar, *t) for t in tareas]
        for f in as_completed(futs):
            f.result()

    log(f"\nResumen: ok={len(ok)} fallos={len(fallos)} tiempo total={time.time() - t_ini:.0f}s")
    for m, e in fallos:
        log(f"  fallo: {m[:60]} -> {e}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    sys.exit(main())
