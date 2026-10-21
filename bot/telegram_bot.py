"""Bot de Telegram (cliente delgado): reenvía cada mensaje a POST {API_BASE_URL}/api/consultas.

Uso:  .venv/Scripts/python -m bot.telegram_bot     (requiere TELEGRAM_BOT_TOKEN en .env)
"""
from __future__ import annotations

import asyncio
import html
import logging
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402

log = logging.getLogger("telegram_bot")

BIENVENIDA = (
    "👋 Hola, soy el asistente de <b>Normativa y Políticas Internas</b>.\n\n"
    "Pregúntame sobre políticas de crédito, autonomías de aprobación, límites, procedimientos de agencia, etc. "
    "Respondo solo con normas vigentes y cito documento, artículo y versión.\n\n"
    "Ejemplo: <i>¿Cuál es el límite de endeudamiento para un crédito de consumo?</i>"
)
CONFIANZA = {"alta": "🟢 Alta", "media": "🟡 Media", "sin_respaldo": "🔴 Sin respaldo"}


def formatear(r: dict) -> str:
    """Convierte la respuesta de /api/consultas en un mensaje HTML de Telegram."""
    e = html.escape
    partes = [e(str(r.get("respuesta") or ""))]
    citas = r.get("citas") or []
    if citas:
        partes.append("\n<b>Fuentes:</b>")
        for c in citas:
            partes.append(f"📄 {e(str(c.get('nombre_norma') or c.get('doc_id') or ''))} · Art. "
                          f"{e(str(c.get('articulo') or '?'))} · v{e(str(c.get('version') or '?'))} · "
                          f"{e(str(c.get('estado') or ''))}")
    adv = r.get("advertencias") or []
    if adv:
        partes.append("\n<b>Advertencias:</b>")
        partes += [f"⚠️ {e(str(a))}" for a in adv]
    partes.append(f"\nConfianza: {CONFIANZA.get(r.get('confianza'), e(str(r.get('confianza'))))}")
    texto = "\n".join(partes)
    return texto if len(texto) <= 4000 else texto[:3990] + "…"


def consultar_api(pregunta: str, usuario: str) -> dict:
    url = get_settings().api_base_url.rstrip("/") + "/api/consultas"
    resp = requests.post(url, json={"pregunta": pregunta, "canal": "telegram", "usuario": usuario}, timeout=90)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    token = get_settings().telegram_bot_token
    if not token:
        print("Falta TELEGRAM_BOT_TOKEN en .env (créalo con @BotFather). El bot no se inicia.")
        sys.exit(1)

    from telegram import Update
    from telegram.constants import ChatAction, ParseMode
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(BIENVENIDA, parse_mode=ParseMode.HTML)

    async def mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            r = await asyncio.to_thread(consultar_api, update.message.text, f"tg:{update.effective_user.id}")
            texto = formatear(r)
        except Exception as ex:
            log.exception("Error consultando la API")
            texto = f"⚠️ No pude consultar el servicio en este momento ({type(ex).__name__}). Intenta de nuevo."
        await update.message.reply_text(texto, parse_mode=ParseMode.HTML)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje))
    log.info("Bot en modo polling. API: %s", get_settings().api_base_url)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
