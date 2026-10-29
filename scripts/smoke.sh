#!/usr/bin/env bash
# =============================================================
# Smoke test end-to-end ai-service (default: compose local di http://localhost:3040).
#   ./scripts/smoke.sh                   # lengkap (butuh LLM terkonfigurasi di service)
#   SMOKE_SKIP_LLM=1 ./scripts/smoke.sh  # tanpa langkah yang butuh LLM/embedding (ingest, search, chat)
# Env: BASE_URL, ADMIN_API_KEYS, API_KEYS, JWT_SECRET (samakan dengan service), MOCK_BE_PORT,
#      SMOKE_NO_MOCK_BE=1 bila service sudah menunjuk BE nyata (BE_BASE_URL).
# Butuh: curl, python3 (parsing JSON + minting JWT). Mock BE butuh `uv` (scripts/mock_be.py).
# =============================================================
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:3040}"
ADMIN_KEY="${ADMIN_API_KEYS:-dev-admin-key}"
API_KEY="${API_KEYS:-dev-api-key}"
JWT_SECRET="${JWT_SECRET:-dev-jwt-secret-samakan-dengan-backend}"
MOCK_BE_PORT="${MOCK_BE_PORT:-3001}"
SKIP_LLM="${SMOKE_SKIP_LLM:-0}"
SAMPLE_DOC="${SAMPLE_DOC:-docs/samples/panduan-pelaporan-sari.md}"
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { printf "${YELLOW}▶ %s${NC}\n" "$*"; }
pass() { printf "${GREEN}  ✔ %s${NC}\n" "$*"; }
fail() { printf "${RED}  ✘ %s${NC}\n" "$*"; exit 1; }

HTTP_BODY=""; HTTP_STATUS=""
http() { # http <method> <path> [curl args...] → mengisi $HTTP_BODY dan $HTTP_STATUS
  local method="$1" path="$2"; shift 2
  local out
  out=$(curl -sS -X "$method" "$BASE_URL$path" -w $'\n%{http_code}' "$@")
  HTTP_STATUS="${out##*$'\n'}"
  HTTP_BODY="${out%$'\n'*}"
}
jq_() { printf '%s' "$HTTP_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1"; }
expect_status() { [ "$HTTP_STATUS" = "$1" ] || fail "$2 (status $HTTP_STATUS): ${HTTP_BODY:0:300}"; }

mint_jwt() { # mint_jwt <sub> [temp]
  python3 - "$1" "${2:-}" "$JWT_SECRET" <<'PY'
import base64, hashlib, hmac, json, sys, time, uuid
sub, temp, secret = int(sys.argv[1]), sys.argv[2] == "temp", sys.argv[3]
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
now = int(time.time())
payload = {"sub": sub, "email": f"user{sub}@example.id", "iat": now, "exp": now + 900}
payload.update({"isTemp": True} if temp else {"roleId": 1, "sessionId": str(uuid.uuid4())})
head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
body = b64(json.dumps(payload, separators=(",", ":")).encode())
sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
print(f"{head}.{body}.{sig}")
PY
}

MOCK_PID=""; CHANNEL_WEB_ID=""
cleanup() {
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null || true
  if [ -n "$CHANNEL_WEB_ID" ]; then
    curl -sS -o /dev/null -X DELETE "$BASE_URL/api/v1/admin/channels/$CHANNEL_WEB_ID" -H "X-API-Key: $ADMIN_KEY" || true
  fi
}
trap cleanup EXIT

step "readyz & docs"
http GET /readyz; expect_status 200 "readyz"
[ "$(jq_ "d['db']")" = "ok" ] || fail "db belum siap: $HTTP_BODY"
pass "readyz: $HTTP_BODY"
http GET /docs; expect_status 200 "swagger /docs (APP_ENV local/dev)"; pass "/docs 200"

if [ "${SMOKE_NO_MOCK_BE:-0}" != "1" ]; then
  step "mock BE (GET /auth/profile) di port $MOCK_BE_PORT"
  JWT_SECRET="$JWT_SECRET" uv run python scripts/mock_be.py --host 0.0.0.0 --port "$MOCK_BE_PORT" >/dev/null 2>&1 &
  MOCK_PID=$!
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://localhost:$MOCK_BE_PORT/docs" && break; sleep 0.5; done
  pass "mock BE aktif (pid $MOCK_PID)"
fi

JWT42=$(mint_jwt 42); JWT43=$(mint_jwt 43); JWT_TEMP=$(mint_jwt 42 temp)

step "identitas web (/me)"
http GET /api/v1/me -H "Authorization: Bearer $JWT42"; expect_status 200 "/me 42"
[ "$(jq_ "d['ai_role']")" = "analyst" ] || fail "sub 42 seharusnya analyst: $HTTP_BODY"
pass "42 → analyst, scope $(jq_ "d['scope_label']")"
http GET /api/v1/me -H "Authorization: Bearer $JWT43"; expect_status 200 "/me 43"
[ "$(jq_ "d['ai_role']")" = "public" ] || fail "sub 43 seharusnya public: $HTTP_BODY"
pass "43 → public, scope $(jq_ "d['scope_label']")"
http GET /api/v1/me -H "Authorization: Bearer $JWT_TEMP"; expect_status 401 "token 2FA sementara harus 401"; pass "token isTemp ditolak"
http GET /api/v1/me; expect_status 401 "tanpa token harus 401"; pass "tanpa token ditolak"

step "admin: kanal & API key"
http GET /api/v1/admin/channels -H "X-API-Key: salah"; expect_status 401 "key admin salah"; pass "key admin salah ditolak"
http POST /api/v1/admin/channels -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"platform":"wa","channel_id":"628123456789","role":"analyst","scope_level":"GLOBAL","display_name":"Smoke WA"}'
[ "$HTTP_STATUS" = "201" ] || [ "$HTTP_STATUS" = "409" ] || fail "buat kanal wa: $HTTP_STATUS $HTTP_BODY"
pass "kanal wa 628123456789 analyst (status $HTTP_STATUS)"

if [ "$SKIP_LLM" = "1" ]; then
  printf "${GREEN}SMOKE TEST LULUS (tanpa langkah LLM/embedding: ingest, search, chat dilewati)${NC}\n"; exit 0
fi

step "ingest dokumen contoh (sync)"
http POST /api/v1/admin/ingest -H "X-API-Key: $ADMIN_KEY" -F "files=@$SAMPLE_DOC" -F "sync=true" -F "tags=sari,juknis"
expect_status 202 "ingest"
st=$(jq_ "d['items'][0]['status']")
[ "$st" = "indexed" ] || [ "$st" = "duplicate" ] || fail "status ingest: $HTTP_BODY"
pass "ingest → $st ($(jq_ "d['items'][0]['document_id']"))"
http POST /api/v1/admin/ingest -H "X-API-Key: $ADMIN_KEY" -F "files=@$SAMPLE_DOC" -F "sync=true"
[ "$(jq_ "d['items'][0]['status']")" = "duplicate" ] || fail "ingest ulang harus duplicate"; pass "ingest ulang → duplicate (idempoten)"
http POST /api/v1/admin/search -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' -d '{"query":"alur pelaporan kasus SARI","top_k":3}'
expect_status 200 "search"
[ "$(jq_ "len(d['hits'])")" -ge 1 ] || fail "search tanpa hasil"; pass "search → $(jq_ "d['hits'][0]['filename']")"

step "internal chat: auth"
http POST /api/v1/internal/chat -H "X-API-Key: salah" -H 'Content-Type: application/json' -d '{"platform":"wa","channel_id":"1","query":"x"}'
expect_status 401 "internal key salah"; pass "key konektor salah ditolak"

step "chat helpdesk sebagai public (43)"
http POST /api/v1/chat -H "Authorization: Bearer $JWT43" -H 'Content-Type: application/json' \
  -d '{"query":"Bagaimana alur pelaporan kasus SARI? Jelaskan langkah pendaftarannya."}'
expect_status 200 "chat helpdesk"
[ "$(jq_ "len(d['sources'])")" -ge 1 ] || fail "jawaban tanpa sumber: ${HTTP_BODY:0:400}"
SESSION=$(jq_ "d['session_id']")
pass "intent=$(jq_ "d['intent']") sumber=$(jq_ "len(d['sources'])") latency=$(jq_ "d['latency_ms']")ms"

step "pertanyaan data sebagai public (43) → harus ditolak & diaudit"
http POST /api/v1/chat -H "Authorization: Bearer $JWT43" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION\",\"query\":\"Berapa jumlah kasus SARI per provinsi tahun 2025?\"}"
expect_status 200 "chat data public"
intent2=$(jq_ "d['intent']")
[ "$intent2" = "denied" ] || fail "intent seharusnya denied (model tidak memanggil tool?): $intent2 — $(jq_ "d['answer'][:200]")"
http GET "/api/v1/admin/sql-audit?principal_id=43&status=access_denied" -H "X-API-Key: $ADMIN_KEY"
[ "$(jq_ "d['total']")" -ge 1 ] || fail "audit access_denied kosong"
http GET "/api/v1/admin/sql-audit?principal_id=43&status=ok" -H "X-API-Key: $ADMIN_KEY"
[ "$(jq_ "d['total']")" -eq 0 ] || fail "public pernah menjalankan SQL!"
pass "ditolak + audit access_denied tercatat, nol query ok"

step "analisis data sebagai analis (42)"
http POST /api/v1/chat -H "Authorization: Bearer $JWT42" -H 'Content-Type: application/json' \
  -d '{"query":"Berapa jumlah kasus SARI per provinsi pada tahun 2025?"}'
expect_status 200 "chat analis"
sqlst=$(jq_ "(d['sql'] or {}).get('status')")
[ "$sqlst" = "ok" ] || [ "$sqlst" = "empty" ] || fail "SQL tool tidak berjalan: status=$sqlst — $(jq_ "d['answer'][:200]")"
pass "sql status=$sqlst baris=$(jq_ "d['sql']['row_count']") scope=$(jq_ "d['sql']['scope_label']")"

step "analis ber-scope faskes (43 di-whitelist FASKES #3)"
http POST /api/v1/admin/channels -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"platform":"web","channel_id":"43","role":"analyst","scope_level":"FASKES","facility_id":3}'
[ "$HTTP_STATUS" = "201" ] || fail "whitelist web 43: $HTTP_STATUS $HTTP_BODY"
CHANNEL_WEB_ID=$(jq_ "d['id']")
JWT43B=$(mint_jwt 43)
http POST /api/v1/chat -H "Authorization: Bearer $JWT43B" -H 'Content-Type: application/json' \
  -d '{"query":"Berapa jumlah kasus SARI per provinsi pada tahun 2025?"}'
expect_status 200 "chat analis faskes"
scope4=$(jq_ "(d['sql'] or {}).get('scope_label', '')")
case "$scope4" in *Faskes*) pass "scope tool = $scope4";; *) fail "scope seharusnya Faskes: '$scope4' intent=$(jq_ "d.get('intent')")";; esac

step "internal chat WA (kanal terdaftar → analyst)"
http POST /api/v1/internal/chat -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"platform":"wa","channel_id":"+62 812-3456-789","query":"Apa definisi operasional SARI?","display_name":"Smoke"}'
expect_status 200 "internal chat"
[ -n "$(jq_ "d['text_plain']")" ] || fail "text_plain kosong"
pass "session=$(jq_ "d['session_id']") role=$(jq_ "d['identity']['ai_role']")"

step "streaming SSE"
events=$(curl -sS -N -X POST "$BASE_URL/api/v1/chat/stream" -H "Authorization: Bearer $JWT42" -H 'Content-Type: application/json' \
  -d '{"query":"Sebutkan jenis spesimen yang diterima untuk SARI."}' | grep -E '^event: ' | sort | uniq -c | tr -s ' ')
printf '%s\n' "$events" | grep -q 'event: token' || fail "tidak ada event token: $events"
printf '%s\n' "$events" | grep -q 'event: done' || fail "tidak ada event done: $events"
pass "event: $(printf '%s' "$events" | tr '\n' ';')"

printf "${GREEN}SMOKE TEST LULUS${NC}\n"
