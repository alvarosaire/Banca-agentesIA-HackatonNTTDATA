"""Packs por sector. Se elige con la variable de entorno SECTOR (generico | banca | mineria | manufactura).

Cada pack es una carpeta sectors/<nombre>/ con:
  __init__.py      -> importa sus tools y define FLOW (agentes + flujo) y CASO_EJEMPLO
  tools.py         -> tools (@tool) y validadores (@validator) del sector
  guardrails.json  -> reglas (tools prohibidas, máximos, umbrales de aprobación)
  ui.json          -> textos de la interfaz (se combinan con sectors/ui_base.json)
  data/*.json      -> datos sintéticos (reemplazables por los reales)
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agents import guardrails
from app.agents.flow import Flow
from app.config import get_settings

SECTORS_DIR = Path(__file__).resolve().parent


@dataclass
class SectorPack:
    name: str
    dir: Path
    flow: Flow
    example: str
    ui: dict[str, Any]


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache
def active() -> SectorPack:
    """Carga (una sola vez) el pack indicado por SECTOR."""
    name = get_settings().sector.strip().lower()
    pack_dir = SECTORS_DIR / name
    if not (pack_dir / "__init__.py").exists():
        disponibles = sorted(p.name for p in SECTORS_DIR.iterdir() if (p / "__init__.py").exists())
        raise RuntimeError(f"SECTOR='{name}' no existe. Disponibles: {disponibles}")
    module = importlib.import_module(f"sectors.{name}")  # registra tools y validadores
    guardrails.RULES_PATH = pack_dir / "guardrails.json"
    ui = _merge(load_json(SECTORS_DIR / "ui_base.json"), load_json(pack_dir / "ui.json"))
    return SectorPack(name=name, dir=pack_dir, flow=module.FLOW, example=module.CASO_EJEMPLO, ui=ui)
