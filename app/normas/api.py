"""Router /api del caso normativa: junta los routers de cada frente de trabajo."""
from fastapi import APIRouter

from app.normas.api_chat import router as chat_router
from app.normas.api_consultas import router as consultas_router
from app.normas.api_documentos import router as documentos_router
from app.normas.api_kpis import router as kpis_router
from app.normas.api_usuarios import router as usuarios_router

router = APIRouter(prefix="/api")
router.include_router(documentos_router)
router.include_router(consultas_router)
router.include_router(chat_router)
router.include_router(kpis_router)
router.include_router(usuarios_router)
