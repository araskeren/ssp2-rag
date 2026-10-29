#!/usr/bin/env bash
# Dijalankan sekali oleh entrypoint image pgvector/pgvector saat volume masih kosong.
# Membuat DB ai_service (+ekstensi vector), DB fixture sdp_fixture, dan role read-only ai_ro.
set -euo pipefail
AI_RO_PASSWORD="${AI_RO_PASSWORD:-ai_ro}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE DATABASE ai_service;
CREATE DATABASE sdp_fixture;
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_ro') THEN
    CREATE ROLE ai_ro LOGIN PASSWORD '${AI_RO_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 6;
  END IF;
END \$\$;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname ai_service -c "CREATE EXTENSION IF NOT EXISTS vector;"
