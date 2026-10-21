"""Trazabilidad: cada ejecución (Run) guarda sus pasos y aprobaciones.

Se mantiene en memoria (para la UI) y se vuelca a runs/<id>.json después de cada cambio.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("trace")

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_ms() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


# Tipos de paso: input, thought, tool_call, guardrail, approval_requested,
# approval_decision, tool_result, handoff, final, error
@dataclass
class Step:
    n: int
    ts: str
    agent: str
    type: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Approval:
    id: str
    run_id: str
    agent: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    tool_call_id: str
    status: str = "pending"  # pending | approved | rejected
    created_at: str = field(default_factory=_now)
    decided_at: str | None = None
    decided_by: str | None = None
    comment: str | None = None


@dataclass
class Run:
    id: str
    case: str
    input: str
    status: str = "running"  # running | waiting_approval | completed | failed
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    steps: list[Step] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    output: str | None = None
    error: str | None = None
    state: dict[str, Any] = field(default_factory=dict)  # estado compartido entre agentes
    meta: dict[str, Any] = field(default_factory=dict)   # agente, canal, usuario, modelo (tabla trazas)
    started_at: str = field(default_factory=_now_ms)    # ISO con milisegundos (KPI tiempo de respuesta)
    finished_at: str | None = None                       # se fija al guardar con status completed/failed

    def add_step(self, agent: str, type_: str, summary: str, **data: Any) -> Step:
        step = Step(n=len(self.steps) + 1, ts=_now(), agent=agent, type=type_, summary=summary, data=data)
        self.steps.append(step)
        self.updated_at = step.ts
        log.info("[run %s] #%d %s/%s: %s", self.id, step.n, agent, type_, summary[:150])
        return step

    def pending_approvals(self) -> list[Approval]:
        return [a for a in self.approvals if a.status == "pending"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunStore:
    """Almacén en memoria + persistencia en JSON. Thread-safe."""

    def __init__(self, persist_dir: Path | None = RUNS_DIR) -> None:
        self._runs: dict[str, Run] = {}
        self.lock = threading.RLock()
        self.persist_dir = persist_dir
        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)

    def create(self, case: str, input_text: str) -> Run:
        with self.lock:
            run = Run(id=_short_id(), case=case, input=input_text)
            self._runs[run.id] = run
            return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def find_approval(self, approval_id: str) -> tuple[Run, Approval] | None:
        for run in self._runs.values():
            for a in run.approvals:
                if a.id == approval_id:
                    return run, a
        return None

    def save(self, run: Run) -> None:
        if run.status in ("completed", "failed") and not run.finished_at:
            run.finished_at = _now_ms()
        elif run.status not in ("completed", "failed"):
            run.finished_at = None  # se reanudó (p. ej. tras una aprobación)
        if not self.persist_dir:
            return
        try:
            path = self.persist_dir / f"{run.id}.json"
            path.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("No se pudo guardar la traza del run %s", run.id)
        try:
            from app.normas import db  # import diferido: evita ciclos
            db.save_trace(run)
        except Exception:
            log.exception("No se pudo guardar la traza del run %s en SQLite", run.id)


store = RunStore()


def new_approval_id() -> str:
    return _short_id()
