#!/usr/bin/env bash
# Mengindeks panduan GitBook turborepo (apps/docs/**/*.md) sebagai dokumen RAG lewat API admin.
#   ./scripts/ingest-panduan.sh [DIR_DOCS]     # default: ../turborepo/apps/docs
#   BASE_URL=http://localhost:3040 ADMIN_API_KEY=... ./scripts/ingest-panduan.sh
# Judul diambil dari H1 pertama (banyak berkas bernama README.md), tag dari folder modul.
# Idempoten: berkas yang isinya tidak berubah dilaporkan `duplicate`.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi
DOCS_DIR="${1:-../turborepo/apps/docs}"
BASE_URL="${BASE_URL:-http://localhost:3040}"
ADMIN_API_KEY="${ADMIN_API_KEY:-${ADMIN_API_KEYS:-dev-admin-key}}"
ADMIN_API_KEY="${ADMIN_API_KEY%%,*}"
[ -d "$DOCS_DIR" ] || { echo "Folder panduan tidak ditemukan: $DOCS_DIR" >&2; exit 1; }

ok=0
gagal=0
while IFS= read -r -d '' berkas; do
  rel="${berkas#"$DOCS_DIR"/}"
  judul="$(grep -m1 '^# ' "$berkas" | sed -E 's/^# +//' || true)"
  [ -n "$judul" ] || judul="$rel"
  modul="${rel%%/*}"
  [ "$modul" = "$rel" ] && modul="umum"
  respons="$(curl -sS --max-time 300 -X POST "$BASE_URL/api/v1/admin/ingest" \
    -H "X-API-Key: $ADMIN_API_KEY" \
    -F "files=@$berkas;type=text/markdown" \
    -F "title=$judul" \
    -F "source=panduan:$rel" \
    -F "tags=panduan,$modul" \
    -F "sync=true")"
  status="$(printf '%s' "$respons" | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(",".join(i.get("status","?") for i in d.get("items",[])) or d.get("message","respons tidak dikenal"))' 2>/dev/null || echo "respons tidak dikenal")"
  case "$status" in
    indexed|duplicate) ok=$((ok + 1)) ;;
    *) gagal=$((gagal + 1)); echo "GAGAL $rel: $status" >&2 ;;
  esac
done < <(find "$DOCS_DIR" -name '*.md' \
  -not -path '*/node_modules/*' -not -path '*/_book/*' -not -path '*/scripts/*' \
  -not -path '*/gitbook-plugin-nar/*' -not -name 'SUMMARY.md' -print0 | sort -z)

echo "Selesai: $ok berhasil, $gagal gagal."
[ "$gagal" -eq 0 ]
