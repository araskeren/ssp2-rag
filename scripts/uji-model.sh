#!/usr/bin/env bash
# Uji end-to-end kandidat model lokal (harness in-process, server tidak disentuh):
#   RAG/helpdesk (identity public, sesi baru) + Text-to-SQL (identity analyst GLOBAL, sesi baru).
# Model dibaca dari .env; skrip mengubah LLM_MODEL per model lalu mengembalikannya.
#
#   ./scripts/uji-model.sh                              # beam, gemma4:31b, gpt-oss:120b, gpt-oss:20b
#   ./scripts/uji-model.sh ollama/beam                  # model tertentu
#   OLL=300 ./scripts/uji-model.sh                      # timeout per model (default 240 dtk)
#
# Log lengkap per model: /tmp/opencode/model-uji/<model>.log
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi

MODELS=("$@")
[ ${#MODELS[@]} -eq 0 ] && MODELS=(ollama/beam ollama/gemma4:31b ollama/gpt-oss:120b ollama/gpt-oss:20b)

RAG_Q="Bagaimana alur pelaporan kasus SARI di aplikasi?"
SQL_Q="Berapa jumlah kasus SARI per provinsi tahun 2025?"
OLL="${OLL:-240}"
LOGDIR="/tmp/opencode/model-uji"; mkdir -p "$LOGDIR"

G=$'\033[0;32m'; R=$'\033[0;31m'; B=$'\033[1m'; N=$'\033[0m'

BK="/tmp/opencode/env.backup.before-model-uji.$(date +%Y%m%d-%H%M%S)"
mkdir -p "$(dirname "$BK")"; cp .env "$BK"
restore_env() { cp "$BK" .env; }
trap restore_env EXIT INT TERM

printf "backup  : %s\n" "$BK"
printf "server  : tidak disentuh (harness in-process, sesi baru per model)\n"

printf "\n${B}HASIL UJI E2E${N}\n"
printf "%-28s %-9s %-9s %-10s %-9s %-9s %s\n" "MODEL" "RAG" "src" "rag_ms" "SQL" "rows" "sql_ms"
printf "%-28s %-9s %-9s %-10s %-9s %-9s %s\n" "---------------------------" "---------" "---" "-------" "----" "----" "-------"

PASS=0; FAIL=0
for m in "${MODELS[@]}"; do
  safe=${m//\//_}; safe=${safe//:/_}
  sed -i "s|^LLM_MODEL=.*$|LLM_MODEL=$m|" .env
  ( source .venv/bin/activate 2>/dev/null; set -a; source .env; set +a;
    timeout "$OLL" python -c "
import asyncio
from ai_service.auth.identity import Identity
from ai_service.auth.scope import UserScope
from ai_service.config import get_settings
from ai_service.services import build_services
from ai_service.db.engine import Database

async def run():
    s=get_settings(); db=Database(s.ai_database_url,s.ai_db_pool_size); svc=await build_services(s,db)
    pub=Identity(principal_id='u_rag', platform='wa', ai_role='public', scope=UserScope(level='GLOBAL'))
    r=await svc.runner.run(pub,'wa_u_rag_$safe','$RAG_Q')
    an=Identity(principal_id='u_sql', platform='wa', ai_role='analyst', scope=UserScope(level='GLOBAL'))
    r2=await svc.runner.run(an,'wa_u_sql_$safe','$SQL_Q')
    await svc.aclose()
    print('RESULT|%s|%d|%d|%s|%d|%d' % (r.intent,len(r.sources),r.latency_ms,r2.intent,(r2.sql or {}).get('row_count') or 0,r2.latency_ms))
asyncio.run(run())
" ) > "$LOGDIR/$safe.log" 2>&1
  out=$(grep -o "RESULT|.*" "$LOGDIR/$safe.log" | tail -1)
  if [ -n "$out" ]; then
    IFS='|' read -r _ r_intent r_src r_ms s_intent s_rows s_ms <<< "$out"
    st_r="FAIL"; st_s="FAIL"
    [ "$r_intent" = helpdesk ] && [ "${r_src:-0}" -ge 1 ] && st_r="PASS"
    [ "$s_intent" = data_analysis ] && [ "${s_rows:-0}" -ge 1 ] && st_s="PASS"
    if [ "$st_r" = PASS ] && [ "$st_s" = PASS ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
    printf "%-28s ${G}%-9s${N} %-9s %-10s ${G}%-9s${N} %-9s %s\n" "$m" "$st_r" "${r_src:-0}" "${r_ms}ms" "$st_s" "${s_rows:-0}" "${s_ms}ms"
    [ "$st_r" = FAIL ] || [ "$st_s" = FAIL ] && printf "  ${R}detail${N}: %s\n" "$out"
  else
    FAIL=$((FAIL+1))
    printf "%-28s ${R}%-9s${N} %-9s %-10s ${R}%-9s${N} %-9s %s\n" "$m" FAIL "-" "-" FAIL "-" "-"
    printf "  ${R}error${N}: lihat $LOGDIR/$safe.log\n"
    tail -3 "$LOGDIR/$safe.log" | sed 's/^/    /'
  fi
done

restore_env; trap - EXIT INT TERM
printf "\n.env dikembalikan -> LLM_MODEL="; grep '^LLM_MODEL=' .env | cut -d= -f2-
printf "${B}RINGKASAN${N}: %d lulus, %d gagal\n" "$PASS" "$FAIL"
[ "$FAIL" = "0" ] || exit 1
