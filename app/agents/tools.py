"""Registro de tools con decorador.

Agregar una tool nueva:

    @tool(description="Consulta X", params={"id": "ID del registro"}, sensitive=False)
    def consultar_x(id: str) -> dict:
        return {...}

El esquema JSON (formato OpenAI function calling) se genera desde la firma de la función.
`sensitive=True` hace que la acción quede pendiente de aprobación humana.
"""
from __future__ import annotations

import inspect
import logging
import typing
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("tools")

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict[str, Any]
    sensitive: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        if t.name in self._tools:
            log.warning("Tool %s redefinida", t.name)
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        selected = names if names is not None else self.names()
        return [self._tools[n].schema() for n in selected if n in self._tools]

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una tool. Nunca lanza excepción: los errores vuelven como {"error": ...}."""
        t = self.get(name)
        if t is None:
            return {"error": f"La herramienta '{name}' no existe"}
        props = t.parameters.get("properties", {})
        missing = [p for p in t.parameters.get("required", []) if p not in args]
        if missing:
            return {"error": f"Faltan argumentos obligatorios: {', '.join(missing)}"}
        try:
            clean = {k: _coerce(v, props[k].get("type")) for k, v in args.items() if k in props}
        except (TypeError, ValueError) as e:
            return {"error": f"Argumento con tipo inválido: {e}"}
        try:
            result = t.func(**clean)
        except Exception as e:  # la tool falla -> el modelo recibe el error y puede corregir
            log.exception("Error ejecutando tool %s", name)
            return {"error": f"{type(e).__name__}: {e}"}
        return result if isinstance(result, dict) else {"resultado": result}


def _coerce(value: Any, json_type: str | None) -> Any:
    """Convierte argumentos al tipo del esquema (el modelo a veces manda "3000" en vez de 3000)."""
    if json_type == "number" and isinstance(value, str):
        return float(value)
    if json_type == "integer" and isinstance(value, (str, float)):
        return int(float(value))
    if json_type == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "1", "si", "sí", "yes")
    return value


registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str | None = None,
    params: dict[str, str] | None = None,
    sensitive: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador que registra una función como tool del agente."""
    params = params or {}

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(func)
        hints = typing.get_type_hints(func)  # resuelve anotaciones en texto (from __future__ import annotations)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, p in sig.parameters.items():
            ann = hints.get(pname, str)
            prop: dict[str, Any] = {"type": _PY_TO_JSON.get(ann, "string")}
            if pname in params:
                prop["description"] = params[pname]
            properties[pname] = prop
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        registry.register(
            Tool(
                name=name or func.__name__,
                description=description or (inspect.getdoc(func) or func.__name__),
                func=func,
                parameters={"type": "object", "properties": properties, "required": required},
                sensitive=sensitive,
            )
        )
        return func

    return decorator
