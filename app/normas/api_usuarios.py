"""Endpoints de usuarios (alta y listado). Contrato en docs/CONTRATOS.md (Áreas de aplicación y usuarios)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.normas import usuarios

router = APIRouter(tags=["usuarios"])


@router.get("/usuarios")
def listar_usuarios() -> list[dict]:
    return usuarios.listar()


class NuevoUsuario(BaseModel):
    usuario: str
    clave: str
    rol: str
    area: str


@router.post("/usuarios")
def crear_usuario(body: NuevoUsuario) -> dict:
    try:
        usuarios.crear(body.usuario, body.clave, body.rol, body.area)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
