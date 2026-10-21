"""Áreas de aplicación de las normas (lista cerrada) y normalización de lo declarado.

    from app.normas.areas import AREAS, normalizar, normalizar_con_descartes
    normalizar("Marketing, call center y RR.HH.")   # -> ["Marketing", "Call Center", "RRHH"]
    normalizar_con_descartes("Ventas; Legal")       # -> (["Legal"], ["Ventas"])

Se compara sin tildes, sin mayúsculas y sin signos; lo que no está en la lista se descarta (y se reporta).
La salida sigue el orden de AREAS (determinístico).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("areas")

AREAS = ["Riesgos", "Créditos", "Agencias", "Cumplimiento", "Marketing", "Cobranzas", "Call Center",
         "Operaciones", "Auditoría", "Legal", "Contabilidad", "Archivo", "RRHH"]

# Variantes obvias (clave ya normalizada: minúsculas, sin tildes ni signos)
ALIAS = {
    "recursoshumanos": "RRHH", "gestionhumana": "RRHH", "talentohumano": "RRHH", "personal": "RRHH",
    "centrodellamadas": "Call Center", "telemarketing": "Call Center", "bancatelefonica": "Call Center",
    "auditoriainterna": "Auditoría", "asesorialegal": "Legal", "asesoriajuridica": "Legal", "juridica": "Legal",
    "cumplimientonormativo": "Cumplimiento", "oficialdecumplimiento": "Cumplimiento",
    "riesgo": "Riesgos", "gestionderiesgos": "Riesgos", "mercadeo": "Marketing", "publicidad": "Marketing",
    "recuperaciones": "Cobranzas", "oficinas": "Agencias", "redagencias": "Agencias",
    "contable": "Contabilidad", "archivocentral": "Archivo",
}
TODAS = {"todas", "todaslasareas", "todaslasarea", "todo", "todoelbanco"}


def _clave(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", t)


def _lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for a in AREAS:
        k = _clave(a)
        for v in (k, k + "s", k + "es", k[:-1] if k.endswith("s") else k, k[:-2] if k.endswith("es") else k):
            out.setdefault(v, a)
    for k, a in ALIAS.items():
        out.setdefault(k, a)
    return out


_LOOKUP = _lookup()


def _partes(valor: Any) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        out: list[str] = []
        for v in valor:
            out += _partes(v)
        return out
    s = str(valor).strip()
    if s.startswith("["):  # lista JSON/YAML en línea: [Marketing, Legal]
        try:
            return _partes(json.loads(s))
        except ValueError:
            s = s.strip("[]")
    return [p.strip().strip("\"'").strip() for p in re.split(r"\s*(?:[,;/|]|\s+y\s+|\s+e\s+)\s*", s) if p.strip()]


def normalizar_con_descartes(valor: Any) -> tuple[list[str], list[str]]:
    """-> (áreas válidas en el orden de AREAS, lo descartado tal como venía)."""
    validas: set[str] = set()
    descartadas: list[str] = []
    for p in _partes(valor):
        k = _clave(p)
        if not k:
            continue
        if k in TODAS:
            validas.update(AREAS)
        elif k in _LOOKUP:
            validas.add(_LOOKUP[k])
        else:
            descartadas.append(p)
    if descartadas:
        log.warning("Áreas fuera de la lista descartadas: %s", descartadas)
    return [a for a in AREAS if a in validas], descartadas


def normalizar(valor: Any) -> list[str]:
    return normalizar_con_descartes(valor)[0]


def area_valida(valor: Any) -> str | None:
    """Una sola área (p. ej. la de un usuario) -> nombre canónico o None."""
    v = normalizar(valor)
    return v[0] if len(v) == 1 else None


def de_json(v: Any) -> list[str]:
    """Columna documentos.areas_aplica (TEXT JSON) -> lista."""
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v) if v else []
    except (TypeError, ValueError):
        return []
    return [a for a in x if isinstance(a, str)] if isinstance(x, list) else []
