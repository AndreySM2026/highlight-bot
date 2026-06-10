#!/usr/bin/env bash
# Установка webhook (нужен VPN или TELEGRAM_PROXY, если api.telegram.org недоступен).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

: "${BOT_TOKEN:?Set BOT_TOKEN in .env or environment}"
: "${WEBHOOK_URL:?Set WEBHOOK_URL in .env or environment}"
: "${WEBHOOK_SECRET:?Set WEBHOOK_SECRET in .env or environment}"

BASE="${WEBHOOK_URL%/}"
URL="${BASE}/webhook/${WEBHOOK_SECRET}"
API_BASE="${TELEGRAM_API_BASE_URL:-https://api.telegram.org}"
API_BASE="${API_BASE%/}"

echo "Setting webhook: ${URL}"
echo "Telegram API: ${API_BASE}"

PAYLOAD=$(WEBHOOK_SECRET="$WEBHOOK_SECRET" URL="$URL" python3 - <<'PY'
import json, os
print(json.dumps({
    "url": os.environ["URL"],
    "secret_token": os.environ["WEBHOOK_SECRET"],
    "drop_pending_updates": True,
}))
PY
)

CURL_ARGS=(-sS --connect-timeout 15 --max-time 60 -X POST)
if [[ -n "${TELEGRAM_PROXY:-}" ]]; then
  CURL_ARGS+=(--proxy "$TELEGRAM_PROXY")
  echo "Using proxy: ${TELEGRAM_PROXY%%@*}"
fi

RESP=$(curl "${CURL_ARGS[@]}" \
  "${API_BASE}/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") || {
  echo ""
  echo "ERROR: не удалось подключиться к ${API_BASE}"
  echo "Включите VPN или задайте TELEGRAM_PROXY=socks5://host:port в .env"
  exit 1
}

echo "$RESP" | python3 -m json.tool
echo ""
echo "Check: curl -s ${API_BASE}/bot\${BOT_TOKEN}/getWebhookInfo | python3 -m json.tool"
