#!/usr/bin/env bash
# Deteksi pergeseran skema DB BE terhadap katalog view ai-service.
# Pakai: ./scripts/check-catalog-drift.sh [DSN]   (default: ANALYTICS_PG_DSN dari .env)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi
uv run ai-service catalog drift ${1:+--dsn "$1"}
