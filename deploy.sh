#!/usr/bin/env bash
# Despliegue repetible en el VPS: git pull -> build -> up -d -> health checks.
# Uso:  ./deploy.sh            (desde la carpeta del repo en el VPS)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERROR: falta .env. Copia .env.example a .env y completa DEEPSEEK_API_KEY y APP_HOST." >&2
  exit 1
fi
# Leer solo las variables necesarias (sin ejecutar .env: puede tener valores con espacios)
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//" || true; }
DEEPSEEK_API_KEY="$(env_get DEEPSEEK_API_KEY)"
APP_HOST="$(env_get APP_HOST)"
CADDY_MODE="$(env_get CADDY_MODE)"
PORT="$(env_get PORT)"
: "${DEEPSEEK_API_KEY:?Falta DEEPSEEK_API_KEY en .env}"
MODE="${CADDY_MODE:-https}"
if [[ "$MODE" == "https" ]]; then
  : "${APP_HOST:?Falta APP_HOST en .env (ej. 203-0-113-5.sslip.io)}"
fi

echo "==> git pull"
git pull --ff-only

echo "==> build"
docker compose build

echo "==> up -d"
docker compose up -d --remove-orphans

echo "==> health check interno (app)"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT:-8000}/health" >/dev/null; then echo "app OK"; break; fi
  [[ $i == 30 ]] && { echo "ERROR: la app no responde"; docker compose logs --tail 50 app; exit 1; }
  sleep 2
done

if [[ "$MODE" == "https" ]]; then
  URL="https://${APP_HOST}"
else
  URL="http://$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
fi

echo "==> health check público ($URL) — la primera vez el certificado puede tardar ~1 min"
for i in $(seq 1 30); do
  if curl -fsS "$URL/health" >/dev/null; then
    echo "OK: $URL/health responde"
    echo "Listo -> $URL"
    exit 0
  fi
  sleep 3
done
echo "ERROR: $URL/health no responde. Revisa: docker compose logs --tail 80 caddy" >&2
[[ "$MODE" == "https" ]] && echo "Plan B: pon CADDY_MODE=http en .env y vuelve a correr ./deploy.sh" >&2
exit 1
