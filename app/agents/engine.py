"""Orquestador: ejecuta un Flow de agentes y aplica decisiones humanas (aprobar / rechazar).

- Cada agente corre en su AgentSession (reanudable si espera aprobación).
- Al terminar un agente: su salida se guarda en el estado compartido (run.state) y se decide
  el siguiente agente (handoff del LLM > regla `next` del Flow > fin).
- El trabajo viaja entre agentes por un AgentTransport (hoy local; punto de extensión NANDA).
- Todos los pasos de todos los agentes quedan en una única traza (run.steps, con su agente).

Con `background=True` el trabajo corre en un hilo y el Run se consulta mientras avanza (API/UI).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.agents.agent import AgentSession
from app.agents.flow import Flow
from app.agents.trace import Run, RunStore, store as default_store
from app.agents.transport import AgentMessage, AgentTransport, LocalTransport

log = logging.getLogger("engine")


@dataclass
class FlowRun:
    flow: Flow
    session: AgentSession
    hops: int = 0


def build_task(message: AgentMessage) -> str:
    """Tarea que recibe el siguiente agente: solicitud original + trabajo previo + instrucciones."""
    previous = "\n".join(f"- {name}: {out}" for name, out in message.state.get("outputs", {}).items())
    return (
        f"Solicitud original:\n{message.state.get('input', '')}\n\n"
        f"Trabajo previo de otros agentes:\n{previous or '- (ninguno)'}\n\n"
        f"Instrucciones de {message.sender} para ti:\n{message.content}"
    )


class Engine:
    def __init__(self, store: RunStore | None = None, transport: AgentTransport | None = None) -> None:
        self.store = store or default_store
        self.transport = transport or LocalTransport()
        self.flows: dict[str, FlowRun] = {}
        self._locks: dict[str, threading.Lock] = {}

    # ---- API pública ---------------------------------------------------------
    def start(self, flow: Flow, input_text: str, case: str = "demo", background: bool = False) -> Run:
        run = self.store.create(case, input_text)
        run.state = {"input": input_text, "outputs": {}}
        session = AgentSession(flow.agent(flow.start), run, input_text)
        self.flows[run.id] = FlowRun(flow, session)
        self._locks[run.id] = threading.Lock()
        self._dispatch(run, session.start, background)
        return run

    def decide(self, approval_id: str, approved: bool, by: str = "humano", comment: str | None = None,
               background: bool = False) -> Run:
        found = self.store.find_approval(approval_id)
        if not found:
            raise KeyError(f"Aprobación {approval_id} no encontrada")
        run, approval = found
        with self.store.lock:  # evita doble decisión (doble clic)
            if approval.status != "pending":
                raise ValueError(f"La aprobación {approval_id} ya fue resuelta ({approval.status})")
            approval.status = "approved" if approved else "rejected"
            approval.decided_by = by
            approval.comment = comment
            approval.decided_at = datetime.now().isoformat(timespec="seconds")
        session = self.flows[run.id].session
        run.status = "running"
        self._dispatch(run, lambda: session.resolve_approval(approval), background)
        return run

    # ---- Interno -------------------------------------------------------------
    def _dispatch(self, run: Run, fn: Callable[[], object], background: bool) -> None:
        def work() -> None:
            with self._locks[run.id]:
                try:
                    fn()
                    self._continue(run)
                except Exception as e:  # nunca dejar un run colgado en "running"
                    log.exception("Error interno en run %s", run.id)
                    run.add_step("orquestador", "error", f"Error interno: {e}")
                    run.status, run.error = "failed", str(e)
                self.store.save(run)

        if background:
            threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
        else:
            work()

    def _continue(self, run: Run) -> None:
        """Avanza el flujo mientras los agentes vayan terminando."""
        fr = self.flows[run.id]
        while True:
            s = fr.session
            if s.status == "waiting_approval":
                run.status = "waiting_approval"
                return
            if s.status == "failed":
                run.status, run.error = "failed", s.output
                return

            # El agente terminó: actualizar estado compartido y decidir el siguiente
            name = s.agent.name
            run.state["outputs"][name] = s.output or ""
            if s.handoff:
                target, instructions = s.handoff
            else:
                target, instructions = fr.flow.next_for(name, run.state), s.output or ""

            if not target:
                run.status, run.output = "completed", s.output
                return
            if fr.hops >= fr.flow.max_handoffs:
                run.add_step("orquestador", "error", f"Límite de {fr.flow.max_handoffs} traspasos alcanzado")
                run.status, run.error = "failed", "Límite de traspasos alcanzado"
                return

            fr.hops += 1
            message = self.transport.deliver(AgentMessage(
                sender=name, recipient=target, content=instructions,
                reason="handoff" if s.handoff else "secuencial", state=run.state,
            ))
            run.add_step(name, "handoff", f"Traspasa a {target}: {instructions}"[:500],
                         to=target, mode=message.reason)
            fr.session = AgentSession(fr.flow.agent(target), run, build_task(message))
            fr.session.start()


engine = Engine()
