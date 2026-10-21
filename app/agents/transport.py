"""AgentTransport: cómo viaja el trabajo de un agente a otro.

El orquestador NUNCA llama a otro agente directamente: arma un `AgentMessage` y lo entrega
con `transport.deliver(...)`. Hoy todos los agentes viven en el mismo proceso (LocalTransport).

Punto de extensión — NANDA (Project NANDA, MIT Media Lab)
---------------------------------------------------------
Para conectar agentes externos vía NANDA se implementa una subclase, por ejemplo:

    class NandaTransport(AgentTransport):
        def deliver(self, message: AgentMessage) -> AgentMessage:
            # 1. Resolver `message.recipient` en el registro/índice de NANDA (descubrimiento del agente).
            # 2. Serializar el mensaje (message.to_dict()) al formato del protocolo y enviarlo.
            # 3. Devolver el mensaje tal como lo recibe el destinatario (o su respuesta).
            raise NotImplementedError

y se pasa al motor:  Engine(transport=NandaTransport(...)).
Los agentes, tools, guardrails y la traza no cambian. NO está implementado todavía.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AgentMessage:
    sender: str                 # agente que entrega el trabajo
    recipient: str              # agente que lo recibe
    content: str                # tarea / instrucciones para el destinatario
    reason: str = ""            # motivo del traspaso
    state: dict[str, Any] = field(default_factory=dict)  # estado compartido del caso

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentTransport(ABC):
    @abstractmethod
    def deliver(self, message: AgentMessage) -> AgentMessage:
        """Entrega el mensaje al agente destinatario y devuelve lo que este recibe."""


class LocalTransport(AgentTransport):
    """Agentes en el mismo proceso: la entrega es inmediata."""

    def deliver(self, message: AgentMessage) -> AgentMessage:
        return message
