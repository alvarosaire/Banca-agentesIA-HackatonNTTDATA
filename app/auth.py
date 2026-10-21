"""Autenticación simple con dos roles: admin (panel completo + chat) y empleado (solo chat).

Usuarios en la tabla `usuarios` (clave PBKDF2-SHA256, rol y área; app/normas/usuarios.py), sembrados desde .env
(ADMIN_USER/ADMIN_PASSWORD, EMPLEADO_USER/EMPLEADO_PASSWORD, MARKETING_USER/MARKETING_PASSWORD).
La sesión es una cookie firmada con HMAC (AUTH_SECRET). Caddy protege cada ruta con forward_auth
contra /auth/check?rol=..., que responde 200 con X-Usuario/X-Rol/X-Area, o redirige a /login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from app.config import get_settings
from app.normas import usuarios as usuarios_db

router = APIRouter(include_in_schema=False)

COOKIE = "sesion"
DURACION_S = 12 * 3600
LOGIN_HTML = Path(__file__).resolve().parent / "static" / "login.html"
NIVEL = {"empleado": 1, "admin": 2}


def _firma(datos: str) -> str:
    return hmac.new(get_settings().auth_secret.encode(), datos.encode(), hashlib.sha256).hexdigest()


def crear_token(usuario: str, rol: str) -> str:
    datos = f"{usuario}|{rol}|{int(time.time()) + DURACION_S}"
    return base64.urlsafe_b64encode(f"{datos}|{_firma(datos)}".encode()).decode()


def leer_token(token: str | None) -> tuple[str, str] | None:
    """Devuelve (usuario, rol) si la cookie es válida y no expiró."""
    if not token:
        return None
    try:
        usuario, rol, exp, firma = base64.urlsafe_b64decode(token.encode()).decode().split("|")
    except Exception:
        return None
    if not hmac.compare_digest(firma, _firma(f"{usuario}|{rol}|{exp}")) or int(exp) < time.time():
        return None
    u = usuarios_db.obtener(usuario)
    if not u or rol not in NIVEL or u["rol"] != rol:
        return None
    return usuario, rol


def area_de(usuario: str | None) -> str | None:
    u = usuarios_db.obtener(usuario)
    return u["area"] if u else None


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(LOGIN_HTML)


@router.post("/login")
def login(usuario: str = Form(...), clave: str = Form(...), next: str = Form("")) -> Response:
    reg = usuarios_db.autenticar(usuario.strip(), clave)
    if not reg:
        return RedirectResponse("/login?error=1" + (f"&next={quote(next)}" if next else ""), status_code=303)
    rol = reg["rol"]
    destino = next if next.startswith("/") and not next.startswith("//") else ""
    if not destino or (rol == "empleado" and not destino.startswith("/chat")):
        destino = "/" if rol == "admin" else "/chat"
    resp = RedirectResponse(destino, status_code=303)
    resp.set_cookie(COOKIE, crear_token(usuario.strip(), rol), max_age=DURACION_S, httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@router.get("/auth/me")
def me(request: Request) -> dict:
    ses = leer_token(request.cookies.get(COOKIE))
    if not ses:
        return {"usuario": None, "rol": None, "area": None}
    return {"usuario": ses[0], "rol": ses[1], "area": area_de(ses[0])}


@router.get("/auth/check")
def check(request: Request, rol: str = "admin") -> Response:
    """Usado por Caddy (forward_auth). 200 = pasa; si no, redirige al login o al chat."""
    uri = request.headers.get("x-forwarded-uri", "/")
    ses = leer_token(request.cookies.get(COOKIE))
    if ses and NIVEL[ses[1]] >= NIVEL.get(rol, 2):
        headers = {"X-Usuario": ses[0], "X-Rol": ses[1]}
        area = area_de(ses[0])
        if area:
            headers["X-Area"] = quote(area)  # cabecera ASCII: "Cr%C3%A9ditos" (api_chat la decodifica)
        return Response(status_code=200, headers=headers)
    if uri.startswith("/api/"):
        return JSONResponse({"detail": "No autorizado"}, status_code=401 if not ses else 403)
    if ses:  # sesión válida pero sin permiso (empleado en el panel) -> al chat
        return RedirectResponse("/chat", status_code=302)
    return RedirectResponse(f"/login?next={quote(uri)}", status_code=302)
