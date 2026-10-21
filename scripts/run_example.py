"""Caso de ejemplo de punta a punta desde consola (sin UI).

Uso: python scripts/run_example.py [--rechazar]     (el sector sale de SECTOR en .env / entorno)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.engine import engine  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402
from sectors import active  # noqa: E402



def print_trace(run) -> None:
    print(f"\n===== Traza run {run.id} | estado: {run.status} =====")
    for s in run.steps:
        print(f"#{s.n:02d} {s.ts[11:]} [{s.agent}] {s.type:<18} {s.summary}")
    if run.output:
        print(f"\n>>> Resultado final:\n{run.output}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rechazar", action="store_true", help="rechazar en vez de aprobar")
    args = ap.parse_args()
    setup_logging("WARNING")

    pack = active()
    print(f"Sector: {pack.name}")
    run = engine.start(pack.flow, pack.example, case="ejemplo")
    while run.status == "waiting_approval":
        for a in run.pending_approvals():
            print(f"\n*** Aprobación pendiente {a.id}: {a.tool}({a.arguments}) — {a.reason}")
            print_trace(run)
            ok = not args.rechazar
            print(f"\n*** Humano {'APRUEBA' if ok else 'RECHAZA'} {a.id}")
            run = engine.decide(a.id, approved=ok, by="supervisor", comment=None if ok else "Monto no presupuestado")
    print_trace(run)
    return 0 if run.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
