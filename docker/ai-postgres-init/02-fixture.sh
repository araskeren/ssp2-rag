#!/usr/bin/env bash
# Memuat skema+data fixture BE (subset) ke DB sdp_fixture dan memberi grant role ai_ro.
# Berkas fixture di-mount dari ../tests/fixtures dan skrip grant dari ../scripts (lihat compose).
set -euo pipefail
AI_RO_PASSWORD="${AI_RO_PASSWORD:-ai_ro}"
if [ -f /fixtures/be_schema_min.sql ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname sdp_fixture -f /fixtures/be_schema_min.sql
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname sdp_fixture -f /fixtures/be_fixture_data.sql
fi
if [ -f /scripts/grant-ai-ro.sql ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname sdp_fixture \
    -v ai_ro_password="$AI_RO_PASSWORD" -v DBNAME=sdp_fixture -f /scripts/grant-ai-ro.sql
fi
