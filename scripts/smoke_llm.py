"""Smoke test del cliente LLM.

Uso:  python scripts/smoke_llm.py [--runs 3] [--model deepseek-flash]

Prueba, con thinking desactivado y activado:
  (a) llamada simple
  (b) tool calling que devuelve un tool_call válido
  (c) ciclo completo: tool_call -> resultado de la tool -> respuesta final
      (valida que el historial con reasoning_content se acepta)
Además verifica offline el parser tolerante de argumentos JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm_client import LLMClient, LLMError, parse_tool_arguments  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Consulta el estado de un pedido por su ID.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "ID del pedido, ej. ORD-123"}},
                "required": ["order_id"],
            },
        },
    }
]
SYSTEM = {"role": "system", "content": "Eres un asistente. Usa las herramientas cuando sea necesario. Responde en español."}
USER_TOOL = {"role": "user", "content": "¿Cuál es el estado del pedido ORD-123?"}


def check_parser() -> bool:
    cases = {
        '{"a": 1}': {"a": 1},
        '```json\n{"a": 1}\n```': {"a": 1},
        'texto {"a": 1} extra': {"a": 1},
        '{"a": 1,}': {"a": 1},
        "": {},
    }
    ok = all(parse_tool_arguments(raw)[0] == exp for raw, exp in cases.items())
    bad_args, bad_err = parse_tool_arguments("{no es json")
    ok = ok and bad_args == {} and bad_err is not None
    print(f"[parser JSON tolerante] {'OK' if ok else 'FALLA'}")
    return ok


def test_simple(llm: LLMClient, thinking: bool) -> tuple[bool, str]:
    r = llm.chat([{"role": "user", "content": "Responde solo con la palabra: pong"}], thinking=thinking)
    return "pong" in r.content.lower(), f"{r.content.strip()[:40]!r} {r.latency_s}s"


def test_tool_call(llm: LLMClient, thinking: bool) -> tuple[bool, str]:
    r = llm.chat([SYSTEM, USER_TOOL], tools=TOOLS, thinking=thinking)
    if not r.tool_calls:
        return False, f"sin tool_calls (content={r.content[:60]!r})"
    tc = r.tool_calls[0]
    ok = tc.name == "get_order_status" and tc.parse_error is None and tc.arguments.get("order_id") == "ORD-123"
    return ok, f"{tc.name}({tc.raw_arguments}) {r.latency_s}s"


def test_round_trip(llm: LLMClient, thinking: bool) -> tuple[bool, str]:
    messages = [SYSTEM, USER_TOOL]
    r1 = llm.chat(messages, tools=TOOLS, thinking=thinking)
    if not r1.tool_calls:
        return False, "sin tool_calls en el primer paso"
    messages.append(r1.assistant_message())
    for tc in r1.tool_calls:
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps({"order_id": "ORD-123", "status": "en tránsito", "eta": "2 días"}),
        })
    r2 = llm.chat(messages, tools=TOOLS, thinking=thinking)
    ok = not r2.tool_calls and "tránsito" in r2.content.lower()
    return ok, f"{r2.content.strip()[:60]!r} {r2.latency_s}s"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    setup_logging("WARNING")

    parser_ok = check_parser()
    llm = LLMClient(model=args.model)
    print(f"Modelo: {llm.model} | runs por prueba: {args.runs}\n")

    tests = [("a) simple", test_simple), ("b) tool_call", test_tool_call), ("c) ida y vuelta", test_round_trip)]
    summary: dict[str, dict[str, int]] = {}
    for thinking in (False, True):
        mode = "thinking ON " if thinking else "thinking OFF"
        for name, fn in tests:
            passed = 0
            for i in range(args.runs):
                try:
                    ok, detail = fn(llm, thinking)
                except LLMError as e:
                    ok, detail = False, f"ERROR: {e}"
                passed += ok
                print(f"[{mode}] {name:16} #{i + 1}: {'OK ' if ok else 'FALLA'} {detail}")
            summary.setdefault(mode, {})[name] = passed

    print("\n=== Resumen ===")
    for mode, res in summary.items():
        print(mode, " | ".join(f"{k}: {v}/{args.runs}" for k, v in res.items()))
    all_ok = parser_ok and all(v == args.runs for res in summary.values() for v in res.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
