"""Punto de entrada FastAPI."""
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import router as api_router
from app.auth import router as auth_router
from app.normas.api import router as normas_router
from app.config import get_settings
from app.logging_config import setup_logging
from sectors import active

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Arrancando %s | sector=%s | modelo=%s", settings.app_name, active().name, settings.llm_model)
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY no está configurada")
    elif settings.llm_provider != "anthropic" and not settings.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY no está configurada")
    if not settings.pinecone_api_key:
        log.warning("PINECONE_API_KEY no está configurada: se usa el índice local de respaldo")
    try:
        from app.normas import usuarios
        usuarios.sembrar()
    except Exception:
        log.exception("No se pudieron sembrar los usuarios desde .env")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(normas_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "sector": active().name, "model": settings.llm_model, "llm_provider": settings.llm_provider}


INDEX = Path(__file__).resolve().parent / "static" / "index.html"


CHAT = Path(__file__).resolve().parent / "static" / "chat.html"


@app.get("/chat", include_in_schema=False)
def chat_page() -> FileResponse:
    return FileResponse(CHAT)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX)
