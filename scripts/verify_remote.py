"""Verifica un despliegue desde FUERA del servidor (solo librería estándar).

Uso: python scripts/verify_remote.py https://203-0-113-5.sslip.io [--rechazar] [--ip 203.0.113.5]

--ip fuerza la resolución DNS del host a esa IP (útil si la red local filtra sslip.io);
el certificado HTTPS se sigue validando normalmente contra el hostname.

1) GET /health   2) lanza el caso de ejemplo   3) espera la aprobación pendiente
4) aprueba (o rechaza)   5) espera el resultado final e imprime la traza
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request


def call(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def wait(base: str, run_id: str, states: set[str], timeout: int = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = call(base, f"/api/runs/{run_id}")
        if run["status"] in states:
            return run
        time.sleep(1.5)
    raise TimeoutError(f"El run {run_id} no llegó a {states}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--rechazar", action="store_true")
    ap.add_argument("--ip", help="forzar la IP del host (saltarse el DNS local)")
    a = ap.parse_args()
    if a.ip:
        _orig = socket.getaddrinfo
        socket.getaddrinfo = lambda host, *args, **kw: _orig(a.ip, *args, **kw)
    base = a.base_url.rstrip("/")

    print("health:", call(base, "/health"))
    run_id = call(base, "/api/runs", {"case": "verificacion"})["id"]
    print("run lanzado:", run_id)

    run = wait(base, run_id, {"waiting_approval", "completed", "failed"})
    pending = [x for x in run["approvals"] if x["status"] == "pending"]
    if not pending:
        print("ERROR: no se generó aprobación pendiente; estado:", run["status"])
        return 1
    ap_ = pending[0]
    print(f"aprobación pendiente {ap_['id']}: {ap_['tool']}({ap_['arguments']}) — {ap_['reason']}")
    call(base, f"/api/approvals/{ap_['id']}",
         {"approved": not a.rechazar, "by": "verificador", "comment": "rechazo de prueba" if a.rechazar else None})

    run = wait(base, run_id, {"completed", "failed"})
    print(f"\n===== Traza {run_id} | estado: {run['status']} =====")
    for s in run["steps"]:
        print(f"#{s['n']:02d} {s['ts'][11:]} [{s['agent']}] {s['type']:<18} {s['summary'][:140]}")
    print("\n>>> Resultado final:\n", run.get("output") or run.get("error"))
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
