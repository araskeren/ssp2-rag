#!/usr/bin/env bash
# Uji cepat satu pertanyaan ke /api/v1/internal/chat.
#
#   ./scripts/uji-chat.sh                          # pertanyaan data sebagai public → harus denied
#   ./scripts/uji-chat.sh 6280000000001            # sebagai analis → harus data_analysis
#   ./scripts/uji-chat.sh 6280000000001 "Halo"     # pertanyaan bebas
#   PLATFORM=tg ./scripts/uji-chat.sh ...          # ganti platform
#
# Env: BASE_URL, API_KEY (default dari .env), PLATFORM (default wa).
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi

BASE_URL="${BASE_URL:-http://localhost:${PORT:-3003}}"
API_KEY_="${API_KEY:-${API_KEYS:-dev-api-key}}"; API_KEY_="${API_KEY_%%,*}"
CH="${1:-620000007777}"
Q="${2:-Berapa jumlah kasus SARI per provinsi 2025?}"
PLATFORM="${PLATFORM:-wa}"

BODY=$(python3 -c 'import json,sys; print(json.dumps({"platform": sys.argv[1], "channel_id": sys.argv[2], "query": sys.argv[3]}))' \
        "$PLATFORM" "$CH" "$Q")
OUT=$(mktemp)

code=$(curl -sS -m "${CHAT_TIMEOUT:-120}" -o "$OUT" -w '%{http_code}' -X POST "$BASE_URL/api/v1/internal/chat" \
  -H "X-API-Key: $API_KEY_" -H 'Content-Type: application/json' -d "$BODY") || {
    echo "server tidak merespons di $BASE_URL" >&2; rm -f "$OUT"; exit 1; }

if [ "$code" != "200" ]; then
  echo "HTTP $code dari $BASE_URL/api/v1/internal/chat" >&2
  head -c 400 "$OUT" >&2; echo >&2; rm -f "$OUT"; exit 1
fi

python3 - "$OUT" "$CH" "$Q" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"kanal   : {sys.argv[2]}")
print(f"tanya   : {sys.argv[3]}")
print(f"intent  : {d.get('intent')}")
print(f"latency : {d.get('latency_ms')} ms")
tools = d.get("tool_calls") or []
for t in tools:
    print(f"tool    : {t.get('name')} status={t.get('status')} {t.get('summary') or ''}")
if not tools:
    print("tool    : (tidak ada)")
s = d.get("sql")
if s:
    print(f"sql     : status={s.get('status')} rows={s.get('row_count')} views={s.get('views')}")
    print(f"query   : {(s.get('query') or '').replace(chr(10), ' ')[:300]}")
print(f"sumber  : {len(d.get('sources') or [])}")
print(f"jawaban : {(d.get('answer') or '')[:300]}")
PY
rm -f "$OUT"
