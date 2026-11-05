#!/usr/bin/env bash
# Cek kondisi ai-service secara utuh dalam satu perintah: proses, probe, konfigurasi,
# isi database, dan (opsional) uji fungsi helpdesk + penolakan analisis data.
#
#   ./scripts/cek-status.sh          # cepat: tanpa memanggil LLM
#   ./scripts/cek-status.sh --full   # + uji chat RAG & RBAC (butuh LLM, ~15 detik)
#
# Env: BASE_URL (default http://localhost:$PORT), ADMIN_API_KEY, API_KEY.
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi

BASE_URL="${BASE_URL:-http://localhost:${PORT:-3003}}"
ADMIN_KEY="${ADMIN_API_KEY:-${ADMIN_API_KEYS:-dev-admin-key}}"; ADMIN_KEY="${ADMIN_KEY%%,*}"
API_KEY_="${API_KEY:-${API_KEYS:-dev-api-key}}"; API_KEY_="${API_KEY_%%,*}"
FULL=0; [ "${1:-}" = "--full" ] && FULL=1

G=$'\033[0;32m'; R=$'\033[0;31m'; Y=$'\033[1;33m'; B=$'\033[1m'; N=$'\033[0m'
ok() { printf "  ${G}OK${N}   %s\n" "$*"; }
bad() { printf "  ${R}GAGAL${N} %s\n" "$*"; }
warn() { printf "  ${Y}--${N}   %s\n" "$*"; }
sec() { printf "\n${B}%s${N}\n" "$*"; }

api() { # api <method> <path> [curl args...]
  curl -sS -m "${HTTP_TIMEOUT:-15}" -X "$1" "$BASE_URL$2" "${@:3}"
}
jqr() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1],{'d':d}))" "$1" 2>/dev/null; }

FAIL=0

sec "1. PROSES & PORT"
PIDS=$(pgrep -f "ai-service serve" | tr '\n' ' ')
if [ -n "$PIDS" ]; then ok "proses ai-service serve: PID ${PIDS% }"; else bad "proses ai-service serve tidak ditemukan"; FAIL=1; fi
if ss -ltn 2>/dev/null | grep -q ":${PORT:-3003} "; then ok "port ${PORT:-3003} mendengarkan"; else bad "port ${PORT:-3003} tidak mendengarkan"; FAIL=1; fi

sec "2. PROBE"
H=$(api GET /healthz); [ "$H" = "ok" ] && ok "/healthz = ok" || { bad "/healthz = $H"; FAIL=1; }
READY=$(api GET /readyz)
DB=$(printf '%s' "$READY" | jqr "d['db']"); AN=$(printf '%s' "$READY" | jqr "d['analytics_db']"); LLM=$(printf '%s' "$READY" | jqr "d['llm']")
[ "$DB" = "ok" ] && ok "database internal = ok" || { bad "database internal = $DB"; FAIL=1; }
[ "$LLM" = "configured" ] && ok "LLM = configured" || { bad "LLM = $LLM"; FAIL=1; }
[ "$AN" = "skipped" ] && warn "analytics_db = skipped (fitur analisis data nonaktif)" || ok "analytics_db = $AN"

sec "3. KONFIGURASI EFEKTIF (\$BASE_URL = $BASE_URL)"
printf "  LLM          : %s @ %s\n" "${LLM_MODEL:-?}" "${LLM_BASE_URL:-?}"
printf "  Embedding    : %s (dim %s)\n" "${EMBEDDING_MODEL:-?}" "${EMBEDDING_DIM:-?}"
printf "  RAG prefetch : %s\n" "${RAG_PREFETCH:-?}"
printf "  SQL backend  : %s\n" "${SQL_BACKEND:-?}"
printf "  AI_SECRETS_KEY: %s\n" "$([ -n "${AI_SECRETS_KEY:-}" ] && echo terisi || echo KOSONG)"
printf "  Analyst      : roles='%s' permission='%s'\n" "${AI_ANALYST_ROLES:-}" "${AI_ANALYST_PERMISSION:-}"

sec "4. DOKUMEN TERINDEKS"
DOCS=$(api GET "/api/v1/admin/documents?limit=5" -H "X-API-Key: $ADMIN_KEY")
DTOTAL=$(printf '%s' "$DOCS" | jqr "d.get('total')")
if [ "${DTOTAL:-0}" -gt 0 ] 2>/dev/null; then
  ok "total dokumen: $DTOTAL"
  printf '%s' "$DOCS" | python3 -c "
import sys,json
for it in json.load(sys.stdin).get('items',[]):
    print(f\"       - {it.get('filename')} | {it.get('status')} | {it.get('chunk_count')} chunk | tags={it.get('tags')}\")
" 2>/dev/null
else warn "belum ada dokumen (ingest: POST /api/v1/admin/ingest)"; fi

sec "5. ISI DATABASE"
PSQL_URL="${AI_DATABASE_URL#postgresql+asyncpg://}"
if [ -n "${PSQL_URL:-}" ] && command -v psql >/dev/null; then
  PGPASSWORD="${PSQL_URL#*:}"; PGPASSWORD="${PGPASSWORD%%@*}"
  PGUSER="${PSQL_URL%%:*}"; PGHOSTPORT="${PSQL_URL##*@}"; PGHOST="${PGHOSTPORT%%:*}"; PGDATA_DB="${PGHOSTPORT#*/}"
  q() { PGPASSWORD="$PGPASSWORD" psql -U "$PGUSER" -h "$PGHOST" -d "$PGDATA_DB" -tAc "$1" 2>/dev/null; }
  printf "  %-18s %s\n" "alembic"          "$(q 'SELECT version_num FROM alembic_version')"
  printf "  %-18s %s\n" "documents"        "$(q 'SELECT count(*) FROM documents')"
  printf "  %-18s %s\n" "document_chunks"  "$(q 'SELECT count(*) FROM document_chunks')"
  printf "  %-18s %s\n" "chat_sessions"    "$(q 'SELECT count(*) FROM chat_sessions')"
  printf "  %-18s %s\n" "chat_messages"    "$(q 'SELECT count(*) FROM chat_messages')"
  printf "  %-18s %s\n" "sql_audit_log"    "$(q 'SELECT count(*) FROM sql_audit_log')"
  printf "  %-18s %s\n" "allowed_channels" "$(q 'SELECT count(*) FROM allowed_channels')"
  printf "  %-18s %s\n" "llm_providers"    "$(q 'SELECT count(*) FROM llm_providers')"
  FLAGS=$(q "SELECT string_agg(key||'='||enabled, ', ') FROM feature_flags")
  printf "  %-18s %s\n" "feature_flags"    "${FLAGS:-'(kosong)'}"
else warn "psql/AI_DATABASE_URL tidak tersedia; bagian ini dilewati"; fi

sec "6. AUDIT AKSES DITOLAK"
AUD=$(api GET "/api/v1/admin/sql-audit?status=access_denied&limit=3" -H "X-API-Key: $ADMIN_KEY")
ATOTAL=$(printf '%s' "$AUD" | jqr "d.get('total')")
printf "  total access_denied: %s\n" "${ATOTAL:-?}"
printf '%s' "$AUD" | python3 -c "
import sys,json
for it in json.load(sys.stdin).get('items',[]):
    print(f\"       - {it.get('principal_id')} ({it.get('ai_role')}) : {(it.get('question') or '')[:70]}\")
" 2>/dev/null

if [ "$FULL" = "1" ]; then
  sec "7. UJI FUNGSI (memanggil LLM, sabar ~15 detik)"
  ask() { curl -sS -m "${CHAT_TIMEOUT:-120}" -X POST "$BASE_URL/api/v1/internal/chat" \
            -H "X-API-Key: $API_KEY_" -H 'Content-Type: application/json' \
            -d "{\"platform\":\"wa\",\"channel_id\":\"$1\",\"query\":$2}"; }

  ASK_CH=6200000001
  R1=$(ask $ASK_CH '"Bagaimana alur pelaporan kasus SARI di aplikasi?"')
  I1=$(printf '%s' "$R1" | jqr "d.get('intent')"); S1=$(printf '%s' "$R1" | jqr "len(d.get('sources') or [])")
  if [ "$I1" = "helpdesk" ] && [ "${S1:-0}" -ge 1 ]; then ok "helpdesk/RAG: intent=$I1 sumber=$S1"
  else bad "helpdesk/RAG: intent=$I1 sumber=$S1"; FAIL=1; fi

  # Kanal acak: riwayat sesi lama bisa membuat model menjawab tanpa memanggil tool,
  # sehingga penolakan tidak terdeteksi.
  R2=$(ask "620000000${RANDOM}" '"Berapa jumlah kasus SARI per provinsi tahun 2025?"')
  I2=$(printf '%s' "$R2" | jqr "d.get('intent')")
  [ "$I2" = "denied" ] && ok "RBAC: pertanyaan data oleh public = denied" || { bad "RBAC: intent=$I2 (harus denied)"; FAIL=1; }

  R3=$(ask $ASK_CH '"Halo"')
  I3=$(printf '%s' "$R3" | jqr "d.get('intent')")
  [ "$I3" = "smalltalk" ] && ok "sapaan: intent=smalltalk" || warn "sapaan: intent=$I3"
else
  printf "\n  (uji chat dilewati — jalankan ./scripts/cek-status.sh --full)\n"
fi

printf "\n${B}RINGKASAN${N}: "
[ "$FAIL" = "0" ] && printf "${G}siap dipakai${N}\n" || printf "${R}ada masalah pada bagian di atas${N}\n"
exit "$FAIL"
