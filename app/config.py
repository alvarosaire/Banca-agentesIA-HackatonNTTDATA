"""Configuración central leída desde variables de entorno (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Hack AI Agents"
    sector: str = "generico"  # generico | banca | mineria | manufactura
    log_level: str = "INFO"

    # Proveedor del LLM: deepseek | anthropic (ambos vía SDK openai, API compatible)
    llm_provider: str = "deepseek"

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1/"

    # Pinecone (vector store). Sin PINECONE_API_KEY se usa un índice local de respaldo.
    pinecone_api_key: str = ""
    pinecone_index: str = "normas"
    pinecone_namespace: str = "demo"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_embed_model: str = "llama-text-embed-v2"
    pinecone_rerank_model: str = "bge-reranker-v2-m3"
    use_rerank: bool = True

    # Normativa: base relacional y canales
    db_path: str = "data/normas.db"
    telegram_bot_token: str = ""
    api_base_url: str = "http://127.0.0.1:8000"   # usado por el bot y Streamlit

    # Usuarios (login): admin = panel completo + chat; empleado = solo chat
    admin_user: str = "admin"
    admin_password: str = ""
    empleado_user: str = "empleado"
    empleado_password: str = ""
    admin_area: str = "Cumplimiento"
    empleado_area: str = "Créditos"
    marketing_user: str = "marketing"
    marketing_password: str = ""
    marketing_area: str = "Marketing"
    auth_secret: str = "cambiar-este-secreto"

    # Comportamiento del cliente LLM
    llm_timeout: float = 60.0          # segundos por request
    llm_max_retries: int = 3           # reintentos ante errores transitorios
    llm_thinking: bool = False         # modo thinking por defecto (configurable por agente)
    llm_reasoning_effort: str = "high"  # low | high | max (solo aplica con thinking)


    @property
    def llm_model(self) -> str:
        return self.anthropic_model if self.llm_provider == "anthropic" else self.deepseek_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
