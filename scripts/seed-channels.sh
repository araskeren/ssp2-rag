#!/usr/bin/env bash
# Mendaftarkan kanal allowed_channels dari berkas CSV (platform,channel_id,role,scope_level,provinsi_kode,kabupaten_kota_kode,facility_id,display_name).
#   ./scripts/seed-channels.sh kanal.csv
# Baris berawalan # diabaikan. Memakai `ai-service channel add` (idempoten: baris yang ada diperbarui).
set -euo pipefail
cd "$(dirname "$0")/.."
CSV="${1:?pakai: $0 <berkas.csv>}"
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi
while IFS=, read -r platform channel_id role scope provinsi kabkota facility display; do
  [ -z "${platform// }" ] && continue
  case "$platform" in \#*) continue;; esac
  args=(channel add "$platform" "$channel_id" --role "${role:-analyst}" --scope "${scope:-GLOBAL}")
  [ -n "${provinsi:-}" ] && args+=(--provinsi-kode "$provinsi")
  [ -n "${kabkota:-}" ] && args+=(--kabupaten-kota-kode "$kabkota")
  [ -n "${facility:-}" ] && args+=(--facility-id "$facility")
  [ -n "${display:-}" ] && args+=(--display-name "$display")
  uv run ai-service "${args[@]}"
done < "$CSV"
