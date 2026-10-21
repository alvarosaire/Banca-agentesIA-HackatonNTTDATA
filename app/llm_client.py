"""Único punto de acceso al LLM (DeepSeek o Anthropic, ambos vía API compatible con OpenAI).

LLM_PROVIDER=deepseek (por defecto) | anthropic. Cambiar de proveedor = cambiar esa variable.

Para cambiar de proveedor o modelo solo se toca este archivo (y las variables de entorno).
El resto del código usa `get_llm().chat(...)` y recibe un `LLMResponse` neutral.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from app.config import get_settings

log = logging.getLogger("llm")

# Errores que vale la pena reintentar
_RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


class LLMError(RuntimeError):
    """Error del LLM tras agotar los reintentos (o error no recuperable)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error: str | None = None  # si el JSON de argumentos no se pudo interpretar


@dataclass
class LLMResponse:
    content: str
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    latency_s: float = 0.0

    def assistant_message(self) -> dict[str, Any]:
        """Mensaje 'assistant' listo para agregar al historial.

        DeepSeek exige devolver `reasoning_content` en las siguientes llamadas cuando
        hay tools y thinking activo; por eso se incluye si existe.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.raw_arguments},
                }
                for tc in self.tool_calls
            ]
        return msg


def parse_tool_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    """Interpreta los argumentos JSON de una tool de forma tolerante.

    Devuelve (argumentos, error). Si no se puede reparar, argumentos = {} y error con el detalle.
    """
    if not raw or not raw.strip():
        return {}, None
    text = raw.strip()
    candidates = [text]
    # Quitar fences de markdown ```json ... ```
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    if fenced != text:
        candidates.append(fenced)
    # Extraer el primer objeto {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    # Comas finales sobrantes
    candidates.append(re.sub(r",\s*([}\]])", r"\1", candidates[-1]))

    last_err = ""
    for c in candidates:
        try:
            value = json.loads(c)
        except json.JSONDecodeError as e:
            last_err = str(e)
            continue
        if isinstance(value, dict):
            return value, None
        last_err = "los argumentos no son un objeto JSON"
    return {}, f"JSON inválido en argumentos: {last_err}"


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        s = get_settings()
        self.provider = s.llm_provider.strip().lower()
        if self.provider == "anthropic":
            key, url, env = api_key or s.anthropic_api_key, base_url or s.anthropic_base_url, "ANTHROPIC_API_KEY"
        else:
            key, url, env = api_key or s.deepseek_api_key, base_url or s.deepseek_base_url, "DEEPSEEK_API_KEY"
        self.model = model or s.llm_model
        self.max_retries = s.llm_max_retries if max_retries is None else max_retries
        self.default_thinking = s.llm_thinking
        self.default_effort = s.llm_reasoning_effort
        if not key:
            raise LLMError(f"Falta {env} en el entorno / .env")
        # max_retries=0 en el SDK: los reintentos los controlamos aquí, con logs.
        self._client = OpenAI(
            api_key=key,
            base_url=url,
            timeout=timeout or s.llm_timeout,
            max_retries=0,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Una llamada de chat. `thinking=None` usa el valor por defecto de la config."""
        thinking = self.default_thinking if thinking is None else thinking
        kwargs: dict[str, Any] = {"model": model or self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if temperature is not None and not thinking:
            kwargs["temperature"] = temperature
        if self.provider == "deepseek":
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
            if thinking:
                kwargs["reasoning_effort"] = reasoning_effort or self.default_effort
        else:
            kwargs["max_tokens"] = 4096

        attempt = 0
        while True:
            attempt += 1
            t0 = time.monotonic()
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except _RETRYABLE as e:
                if attempt > self.max_retries:
                    raise LLMError(f"LLM falló tras {attempt} intentos: {e}") from e
                delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                log.warning("LLM error transitorio (%s), reintento %d en %.1fs", type(e).__name__, attempt, delay)
                time.sleep(delay)
            except APIStatusError as e:  # 4xx no recuperables (400, 401, 402...)
                raise LLMError(f"LLM error {e.status_code}: {e.message}") from e

        latency = time.monotonic() - t0
        choice = resp.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            args, err = parse_tool_arguments(tc.function.arguments)
            if err:
                log.warning("Tool %s con argumentos inválidos: %s", tc.function.name, err)
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                    raw_arguments=tc.function.arguments or "{}",
                    parse_error=err,
                )
            )
        result = LLMResponse(
            content=msg.content or "",
            reasoning_content=getattr(msg, "reasoning_content", None),
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=resp.usage.model_dump() if resp.usage else None,
            latency_s=round(latency, 2),
        )
        log.info(
            "LLM %s thinking=%s tools=%d -> tool_calls=%d finish=%s %.1fs",
            kwargs["model"], thinking, len(tools or []), len(tool_calls), choice.finish_reason, latency,
        )
        return result


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient()
