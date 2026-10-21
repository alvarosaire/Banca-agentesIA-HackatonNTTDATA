"""Definición de un flujo multi-agente.

    Flow(
        agents=[analista, ejecutor],
        start="analista",
        next={"analista": "ejecutor"},             # secuencial: al terminar pasa al siguiente
        # next={"analista": lambda state: ...},    # condicional por regla (devuelve nombre o None)
    )

Además, un agente con `handoffs=["ejecutor"]` recibe la tool `transferir_a` y puede decidir
él mismo traspasar el trabajo (handoff condicional decidido por el LLM). El handoff del LLM
tiene prioridad sobre `next`. Sin `next` ni handoff, el flujo termina.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.agent import Agent

NextRule = str | Callable[[dict[str, Any]], "str | None"] | None


@dataclass
class Flow:
    agents: list[Agent]
    start: str | None = None
    next: dict[str, NextRule] = field(default_factory=dict)
    max_handoffs: int = 6  # evita ciclos infinitos entre agentes

    def __post_init__(self) -> None:
        self.by_name = {a.name: a for a in self.agents}
        self.start = self.start or self.agents[0].name
        for a in self.agents:
            for target in a.handoffs:
                if target not in self.by_name:
                    raise ValueError(f"{a.name} tiene handoff a '{target}', que no está en el flujo")

    def agent(self, name: str) -> Agent:
        return self.by_name[name]

    def next_for(self, agent_name: str, state: dict[str, Any]) -> str | None:
        rule = self.next.get(agent_name)
        return rule(state) if callable(rule) else rule
