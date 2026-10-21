"""Loop de agente con tool use (formato OpenAI function calling).

    mensaje -> el modelo decide -> guardrails -> ejecuta tool (o pide aprobación)
            -> devuelve resultado -> continúa ... hasta respuesta final o max_iterations

La sesión es reanudable: si una tool requiere aprobación humana, la sesión queda en
"waiting_approval" y se continúa con `resolve_approval(...)`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents import guardrails
from app.agents.tools import registry
from app.agents.trace import Approval, Run, new_approval_id
from app.llm_client import LLMClient, LLMError, ToolCall, get_llm

log = logging.getLogger("agent")

BASE_INSTRUCTIONS = (
    "Responde siempre en español. No inventes datos: usa las herramientas para obtenerlos. "
    "Antes de llamar a una herramienta, escribe en una frase breve qué vas a hacer y por qué. "
    "Si una herramienta devuelve un error o un bloqueo, explícalo y no lo intentes evadir."
)


@dataclass
class Agent:
    """Configuración de un agente. Crear uno nuevo = instanciar esta clase."""

    name: str
    role: str                                   # prompt de sistema: rol y objetivo
    tools: list[str] = field(default_factory=list)
    thinking: bool | None = None                # None -> valor global (LLM_THINKING, OFF por defecto)
    reasoning_effort: str | None = None
    max_iterations: int = 8
    temperature: float | None = 0.2
    handoffs: list[str] = field(default_factory=list)  # agentes a los que puede traspasar el trabajo

    def system_prompt(self) -> str:
        prompt = f"{self.role}\n\n{BASE_INSTRUCTIONS}"
        if self.handoffs:
            prompt += (f"\nSi el caso requiere trabajo de otro agente ({', '.join(self.handoffs)}), "
                       f"usa la herramienta {HANDOFF_TOOL} con instrucciones precisas en vez de responder.")
        return prompt


HANDOFF_TOOL = "transferir_a"


def handoff_schema(targets: list[str]) -> dict[str, Any]:
    """Tool interna que permite al agente traspasar el trabajo (handoff condicional)."""
    return {
        "type": "function",
        "function": {
            "name": HANDOFF_TOOL,
            "description": "Traspasa el caso a otro agente con instrucciones claras de lo que debe hacer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agente": {"type": "string", "enum": targets},
                    "instrucciones": {"type": "string", "description": "Qué debe hacer el otro agente y con qué datos"},
                },
                "required": ["agente", "instrucciones"],
            },
        },
    }


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AgentSession:
    """Ejecución de un agente sobre una tarea, registrando cada paso en el Run."""

    def __init__(self, agent: Agent, run: Run, task: str, llm: LLMClient | None = None,
                 rules: guardrails.Rules | None = None) -> None:
        self.agent = agent
        self.run = run
        self.llm = llm or get_llm()
        self.rules = rules or guardrails.Rules.load()
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent.system_prompt()},
            {"role": "user", "content": task},
        ]
        self.pending_calls: list[ToolCall] = []
        self.waiting_approval_id: str | None = None
        self.iterations = 0
        self.status = "idle"  # idle | running | waiting_approval | done | failed
        self.output: str | None = None
        self.handoff: tuple[str, str] | None = None  # (agente destino, instrucciones)

    # ---- API pública -------------------------------------------------------
    def start(self) -> str:
        self.run.add_step(self.agent.name, "input", _short(self.messages[1]["content"]))
        return self.advance()

    def resolve_approval(self, approval: Approval) -> str:
        """Continúa la sesión después de que un humano aprobó o rechazó la acción."""
        if self.status != "waiting_approval" or approval.id != self.waiting_approval_id:
            raise ValueError("Esta sesión no espera esa aprobación")
        tc = self.pending_calls.pop(0)
        verdict = "APROBADA" if approval.status == "approved" else "RECHAZADA"
        self.run.add_step(self.agent.name, "approval_decision",
                          f"{verdict} por {approval.decided_by or 'humano'}: {tc.name}"
                          + (f" — {approval.comment}" if approval.comment else ""),
                          approval_id=approval.id)
        if approval.status == "approved":
            self._execute(tc, aprobacion=f"Aprobada por {approval.decided_by or 'humano'}"
                          + (f": {approval.comment}" if approval.comment else ""))
        else:
            self._tool_message(tc, {"error": "Acción rechazada por el aprobador humano",
                                    "comentario": approval.comment or ""})
        self.waiting_approval_id = None
        return self.advance()

    # ---- Loop ---------------------------------------------------------------
    def advance(self) -> str:
        self.status = "running"
        try:
            while True:
                # 1) Atender tool calls pendientes del último mensaje del modelo
                while self.pending_calls:
                    if not self._handle_tool_call(self.pending_calls[0]):
                        self.status = "waiting_approval"
                        return self.status
                    self.pending_calls.pop(0)
                    if self.handoff:  # traspaso: este agente termina aquí
                        self.pending_calls.clear()
                        self.output = self.handoff[1]
                        self.status = "done"
                        return self.status

                # 2) Pedir al modelo el siguiente paso
                if self.iterations >= self.agent.max_iterations:
                    return self._fail(f"Se alcanzó el máximo de {self.agent.max_iterations} iteraciones")
                self.iterations += 1
                resp = self.llm.chat(
                    self.messages,
                    tools=self._tool_schemas(),
                    thinking=self.agent.thinking,
                    reasoning_effort=self.agent.reasoning_effort,
                    temperature=self.agent.temperature,
                )
                self.messages.append(resp.assistant_message())

                if resp.tool_calls:
                    thought = resp.reasoning_content or resp.content
                    if thought.strip():
                        self.run.add_step(self.agent.name, "thought", _short(thought),
                                          full=thought, latency_s=resp.latency_s)
                    self.pending_calls = list(resp.tool_calls)
                    continue

                self.output = resp.content.strip()
                self.run.add_step(self.agent.name, "final", _short(self.output, 500),
                                  full=self.output, latency_s=resp.latency_s)
                self.status = "done"
                return self.status
        except LLMError as e:
            return self._fail(str(e))

    def _handle_tool_call(self, tc: ToolCall) -> bool:
        """Procesa una tool call. Devuelve False si queda esperando aprobación."""
        args_txt = json.dumps(tc.arguments, ensure_ascii=False)
        self.run.add_step(self.agent.name, "tool_call", f"{tc.name}({args_txt})",
                          tool=tc.name, arguments=tc.arguments)

        if tc.parse_error:
            self.run.add_step(self.agent.name, "error", tc.parse_error, raw=tc.raw_arguments)
            self._tool_message(tc, {"error": tc.parse_error + ". Reintenta con JSON válido."})
            return True

        if tc.name == HANDOFF_TOOL and self.agent.handoffs:
            target = str(tc.arguments.get("agente", ""))
            if target not in self.agent.handoffs:
                self._tool_message(tc, {"error": f"Agente '{target}' no válido. Opciones: {self.agent.handoffs}"})
                return True
            self.handoff = (target, str(tc.arguments.get("instrucciones", "")))
            self._tool_message(tc, {"ok": f"Caso traspasado a {target}"})
            return True

        t = registry.get(tc.name)
        if t is None or tc.name not in self.agent.tools:
            self._tool_message(tc, {"error": f"Herramienta '{tc.name}' no disponible para este agente"})
            return True

        decision = guardrails.check(t, tc.arguments, self.rules)
        if decision.action == "block":
            self.run.add_step(self.agent.name, "guardrail", f"BLOQUEADO: {decision.reason}", tool=tc.name)
            self._tool_message(tc, {"error": f"Bloqueado por política: {decision.reason}"})
            return True
        if decision.action == "approve":
            approval = Approval(id=new_approval_id(), run_id=self.run.id, agent=self.agent.name,
                                tool=tc.name, arguments=tc.arguments, reason=decision.reason,
                                tool_call_id=tc.id)
            self.run.approvals.append(approval)
            self.waiting_approval_id = approval.id
            self.run.add_step(self.agent.name, "approval_requested",
                              f"Pendiente de aprobación: {tc.name} ({decision.reason})",
                              approval_id=approval.id)
            return False

        self._execute(tc)
        return True

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        schemas = registry.schemas(self.agent.tools)
        if self.agent.handoffs:
            schemas.append(handoff_schema(self.agent.handoffs))
        return schemas or None

    def _execute(self, tc: ToolCall, **extra: Any) -> None:
        result = {**registry.execute(tc.name, tc.arguments), **extra}
        self.run.add_step(self.agent.name, "tool_result",
                          _short(json.dumps(result, ensure_ascii=False)), tool=tc.name, result=result)
        self._tool_message(tc, result)

    def _tool_message(self, tc: ToolCall, content: dict[str, Any]) -> None:
        self.messages.append({"role": "tool", "tool_call_id": tc.id,
                              "content": json.dumps(content, ensure_ascii=False)})

    def _fail(self, reason: str) -> str:
        self.run.add_step(self.agent.name, "error", reason)
        self.status = "failed"
        self.output = reason
        return self.status
