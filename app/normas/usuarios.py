"""Usuarios del login (tabla `usuarios`) con clave PBKDF2-SHA256 y área de trabajo.

Al primer uso (y al iniciar la app) se siembran desde .env los que aún no existen:
ADMIN_USER/ADMIN_PASSWORD (admin, ADMIN_AREA), EMPLEADO_USER/EMPLEADO_PASSWORD (empleado, EMPLEADO_AREA) y
MARKETING_USER/MARKETING_PASSWORD (empleado, MARKETING_AREA). Una clave vacía en .env = ese usuario no se siembra.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from typing import Any

from app.config import get_settings
from app.normas import db
from app.normas.areas import area_valida

log = logging.getLogger("usuarios")

ROLES = ("admin", "empleado")
ITERACIONES = 200_000
MIN_CLAVE = 8
_sembrado = False
_lock = threading.Lock()


def hash_clave(clave: str, iteraciones: int = ITERACIONES) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, iteraciones)
    return f"pbkdf2_sha256${iteraciones}${sal.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str | None) -> bool:
    try:
        alg, it, sal, h = (guardado or "").split("$")
        if alg != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def sembrar() -> None:
    """Crea los usuarios de .env que no existan (no toca los existentes)."""
    global _sembrado
    if _sembrado:
        return
    with _lock:
        if _sembrado:
            return
        s = get_settings()
        semillas = [(s.admin_user, s.admin_password, "admin", s.admin_area),
                    (s.empleado_user, s.empleado_password, "empleado", s.empleado_area),
                    (s.marketing_user, s.marketing_password, "empleado", s.marketing_area)]
        for usuario, clave, rol, area in semillas:
            if not usuario or not clave:
                continue
            if db.query("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)):
                continue
            db.execute("INSERT OR IGNORE INTO usuarios (usuario, clave_hash, rol, area, created_at) VALUES (?,?,?,?,?)",
                       (usuario, hash_clave(clave), rol, area_valida(area), db.now()))
            log.info("Usuario sembrado desde .env: %s (%s, %s)", usuario, rol, area_valida(area))
        _sembrado = True


def obtener(usuario: str | None) -> dict[str, Any] | None:
    if not usuario:
        return None
    sembrar()
    rows = db.query("SELECT usuario, clave_hash, rol, area, created_at FROM usuarios WHERE usuario = ?",
                    (usuario.strip(),))
    return rows[0] if rows else None


def autenticar(usuario: str, clave: str) -> dict[str, Any] | None:
    u = obtener(usuario)
    if not u or not verificar_clave(clave, u["clave_hash"]):
        return None
    return u


def listar() -> list[dict[str, Any]]:
    sembrar()
    return db.query("SELECT usuario, rol, area, created_at FROM usuarios ORDER BY created_at, usuario")


def crear(usuario: str, clave: str, rol: str, area: str) -> None:
    """ValueError con el motivo si algo no es válido o el usuario ya existe."""
    sembrar()
    usuario = (usuario or "").strip()
    if not usuario or "|" in usuario or ":" in usuario or len(usuario) > 60:
        raise ValueError("usuario inválido (no vacío, sin '|' ni ':', máx. 60 caracteres)")
    if len(clave or "") < MIN_CLAVE:
        raise ValueError(f"la clave debe tener al menos {MIN_CLAVE} caracteres")
    if rol not in ROLES:
        raise ValueError(f"rol debe ser uno de {ROLES}")
    a = area_valida(area)
    if not a:
        raise ValueError(f"área '{area}' no está en la lista")
    if db.query("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)):
        raise ValueError(f"el usuario '{usuario}' ya existe")
    db.execute("INSERT INTO usuarios (usuario, clave_hash, rol, area, created_at) VALUES (?,?,?,?,?)",
               (usuario, hash_clave(clave), rol, a, db.now()))
