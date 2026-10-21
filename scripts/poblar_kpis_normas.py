"""Pobla las métricas/KPIs con interacciones REALES contra los agentes, sobre el corpus normas_demo.

Envía consultas y casos a POST /api/chat como distintos usuarios (con su área), igual que el chat:
nada se inventa, todo lo registran los agentes (consultas, trazas, brechas, áreas).

    python scripts/poblar_kpis_normas.py --base http://127.0.0.1:8000 [--hilos 3] [--reset-consultas]
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USUARIOS = [("empleado", "Créditos"), ("marketing", "Marketing")]

CUBIERTAS = [
    "¿Cuál es el límite cuota-ingreso para un crédito de consumo?",
    "¿En cuántos días puedo refinanciar un crédito?",
    "¿Cuánto tiempo guardo un expediente de crédito?",
    "¿Hasta qué monto aprueba una agencia?",
    "¿Cuál es la provisión para un crédito Deficiente?",
    "¿Cuándo debo reportar a la central de riesgos?",
    "¿En cuánto tiempo se reporta una operación sospechosa?",
    "¿Qué antigüedad laboral mínima se exige al solicitante?",
    "¿Cuántos días de atraso tiene la categoría Normal?",
    "¿Cuál es el plazo máximo de un crédito hipotecario?",
]
SIN_COBERTURA = [
    "¿Cuál es el límite para créditos vehiculares?",
    "¿Qué requisitos hay para tarjetas de crédito?",
    "¿Cómo se evalúa un crédito agropecuario?",
    "¿Qué norma regula el seguro de desgravamen en consumo?",
    "¿Cuál es la política para créditos a no residentes?",
    "¿Cómo se manejan los créditos con criptoactivos como garantía?",
]
CASOS = [
    "Solicitud de crédito de consumo por S/ 25,000 a 36 meses en la Agencia Miraflores. Cliente dependiente con "
    "4 meses de antigüedad laboral, ingreso neto mensual S/ 4,000, cuota del nuevo crédito S/ 1,100 y otras deudas "
    "con cuota de S/ 800 (cuota total 47.5% del ingreso). La agencia aprobó la operación dentro de su facultad. "
    "¿Procede según las normas vigentes?",
    "Cliente con crédito de consumo en atraso de 40 días solicita refinanciar hoy. La agencia propone refinanciar y "
    "mantener al cliente en categoría Normal; el expediente se conservará 5 años desde la cancelación. "
    "¿Procede la refinanciación y la clasificación según la normativa vigente?",
]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def reset_consultas() -> None:
    from app.normas import casos, db
    casos.ensure_schema()
    with db.connect() as con:
        for sql in ("DELETE FROM consultas", "DELETE FROM brechas", "DELETE FROM chat_mensajes",
                    "DELETE FROM trazas WHERE agente IN ('consultas', 'analista_casos')"):
            log(f"  {sql}: {con.execute(sql).rowcount} filas")


def enviar(base: str, usuario: str, area: str, session_id: str, mensaje: str) -> dict:
    # Mismos encabezados que agrega Caddy tras el login (X-Area va percent-encoded)
    hdr = {"X-Usuario": usuario, "X-Rol": "empleado", "X-Area": quote(area)}
    r = requests.post(f"{base}/api/chat", data={"session_id": session_id, "mensaje": mensaje}, headers=hdr,
                      timeout=180)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--hilos", type=int, default=3)
    ap.add_argument("--reset-consultas", action="store_true")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    if a.reset_consultas:
        reset_consultas()

    random.seed(17)
    tareas: list[tuple[str, str, str, str]] = []
    for q in CUBIERTAS:  # cada pregunta cubierta, por ambos usuarios
        for u, ar in USUARIOS:
            tareas.append((u, ar, f"{u}:demo-{uuid.uuid4().hex[:6]}", q))
    for q in SIN_COBERTURA:  # 2-3 veces cada una (tendencia de brechas)
        for _ in range(random.choice([2, 3])):
            u, ar = random.choice(USUARIOS)
            tareas.append((u, ar, f"{u}:demo-{uuid.uuid4().hex[:6]}", q))
    for c in CASOS:
        tareas.append(("empleado", "Créditos", f"empleado:caso-{uuid.uuid4().hex[:6]}", c))
    random.shuffle(tareas)

    ok, fallos, t0 = 0, [], time.time()
    with ThreadPoolExecutor(max_workers=a.hilos) as ex:
        futs = {ex.submit(enviar, base, *t): t for t in tareas}
        for i, f in enumerate(as_completed(futs), 1):
            u, _, _, msg = futs[f]
            try:
                r = f.result()
                ok += 1
                log(f"[{i}/{len(tareas)}] {u:<9} {r.get('tipo'):<8} {r.get('confianza'):<12} {msg[:60]}")
            except Exception as e:
                fallos.append((msg, str(e)))
                log(f"[{i}/{len(tareas)}] FALLO {msg[:60]}: {e}")
    log(f"Resumen: ok={ok} fallos={len(fallos)} tiempo={time.time() - t0:.0f}s")
    return 0 if not fallos else 1


if __name__ == "__main__":
    sys.exit(main())
