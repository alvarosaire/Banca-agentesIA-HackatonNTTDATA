"""Reglas / guardrails que se evalúan ANTES de ejecutar cada tool.

Fuentes de reglas:
  1. sectors/<SECTOR>/guardrails.json (editable sin tocar código):
       blocked_tools          -> tools prohibidas
       max_values             -> {"monto": 50000}: si un argumento supera el máximo, se bloquea
       approval_thresholds    -> {"monto": 10000}: si un argumento supera el umbral, requiere aprobación
       require_approval_tools -> tools que siempre requieren aprobación humana
  2. Validadores en Python registrados con @validator("nombre_tool"):
       devuelven un mensaje de error (bloquea) o None (ok).
  3. Tools marcadas con @tool(sensitive=True) -> siempre requieren aprobación.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from app.agents.tools import Tool

log = logging.getLogger("guardrails")

# La define el pack de sector activo (sectors/__init__.py) al cargarse
RULES_PATH: Path | None = None

Validator = Callable[[dict[str, Any]], "str | None"]
_validators: dict[str, list[Validator]] = {}


def validator(tool_name: str) -> Callable[[Validator], Validator]:
    """Registra una validación previa a la ejecución de una tool."""

    def decorator(fn: Validator) -> Validator:
        _validators.setdefault(tool_name, []).append(fn)
        return fn

    return decorator


@dataclass
class Decision:
    action: Literal["allow", "approve", "block"]
    reason: str = ""


@dataclass
class Rules:
    blocked_tools: list[str] = field(default_factory=list)
    max_values: dict[str, float] = field(default_factory=dict)
    approval_thresholds: dict[str, float] = field(default_factory=dict)
    require_approval_tools: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Rules":
        path = path or RULES_PATH
        if path is None or not path.exists():
            log.warning("No existe %s, se usan reglas vacías", path)
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _as_number(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(t: Tool, args: dict[str, Any], rules: Rules) -> Decision:
    if t.name in rules.blocked_tools:
        return Decision("block", f"La herramienta '{t.name}' está prohibida por política")

    for fn in _validators.get(t.name, []):
        error = fn(args)
        if error:
            return Decision("block", error)

    for arg, limit in rules.max_values.items():
        n = _as_number(args.get(arg))
        if n is not None and n > limit:
            return Decision("block", f"'{arg}'={n:g} supera el máximo permitido ({limit:g})")

    if t.sensitive:
        return Decision("approve", f"'{t.name}' es una acción sensible")
    if t.name in rules.require_approval_tools:
        return Decision("approve", f"La política exige aprobación para '{t.name}'")
    for arg, threshold in rules.approval_thresholds.items():
        n = _as_number(args.get(arg))
        if n is not None and n > threshold:
            return Decision("approve", f"'{arg}'={n:g} supera el umbral de aprobación ({threshold:g})")

    return Decision("allow")
