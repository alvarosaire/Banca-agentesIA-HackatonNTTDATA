#!/usr/bin/env bash
# Levanta los canales del frente B (la API debe estar corriendo en API_BASE_URL, por defecto :8000).
#   bash scripts/run_ui.sh ui     -> Streamlit en :8501
#   bash scripts/run_ui.sh bot    -> bot de Telegram (polling; requiere TELEGRAM_BOT_TOKEN en .env)
#   bash scripts/run_ui.sh all    -> ambos
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
case "${1:-all}" in
  ui)  $PY -m streamlit run ui/streamlit_app.py --server.headless true --server.port "${UI_PORT:-8501}" --server.address 0.0.0.0 ;;
  bot) $PY -m bot.telegram_bot ;;
  all) $PY -m bot.telegram_bot & $PY -m streamlit run ui/streamlit_app.py --server.headless true --server.port "${UI_PORT:-8501}" --server.address 0.0.0.0 ;;
esac
