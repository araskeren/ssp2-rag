# Deployment ai-service

## 1. Topologi

| | dev | prod |
|---|---|---|
| Network docker | `ssp2-dev-net` (eksternal, dibuat compose turborepo) | `ssp2-prod-net` (eksternal) |
| Service | `ssp2_dev_ai` host `:3040` | 2 replica (`AI_REPLICAS`), host `3006-3007` (`AI_PORT_RANGE`), alias `ssp2_prod_ai` |
| Postgres ai-service | `ssp2_dev_ai_postgres` host `:3150` | `ssp2_prod_ai_postgres` host `127.0.0.1:3312` |
| BE (profil) | `http://ssp2_dev_backend_1:3000` | `http://ssp2_prod_nginx/api/v1` (LB replica BE) |
| DB analitik | `ssp2_dev_postgres` / Postgres dev, role `ai_ro` | Postgres BE prod, role `ai_ro` |
| LLM | endpoint apa pun (`LLM_BASE_URL`) | **on-prem** vLLM/Ollama (privasi data) |
| nginx | `location /ai/` → upstream `ssp2_ai` | sama; internal `:8080` untuk konektor |

## 2. Prasyarat (sekali)

1. Registry GitLab: proyek ai-service, CI file `deploy/gitlab-ci.yml`, variabel `SSH_*`, `SERVER_IP`, `DEPLOY_DIR`.
2. Server: Docker 27+, folder `DEPLOY_DIR` berisi `docker/` dan `.env` (lihat §3), network
   `ssp2-dev-net`/`ssp2-prod-net` sudah ada.
3. DBA menjalankan `scripts/grant-ai-ro.sql` pada DB BE (lihat docs/integration.md §3) dan
   memberi `AI_RO_PASSWORD`.
4. Endpoint LLM on-prem yang mendukung tool calling (vLLM: `--enable-auto-tool-choice --tool-call-parser hermes`;
   Ollama: model qwen2.5/qwen3/llama3.1) dan endpoint embedding (`bge-m3` → `EMBEDDING_DIM=1024`,
   `EMBEDDING_SEND_DIMENSIONS=false`).

## 3. `.env` di server

Salin `.env.example`, lalu isi minimal:

| Variabel | dev | prod |
|---|---|---|
| `DOCKER_IMAGE_AI`, `IMAGE_TAG` | opsional (boleh build) | wajib (`registry.gitlab.com/…/ai-service`, tag di-export CI) |
| `JWT_SECRET` | = BE dev | = BE prod (wajib) |
| `API_KEYS`, `ADMIN_API_KEYS` | boleh default | wajib, acak (`openssl rand -hex 24`) |
| `AI_PG_PASSWORD` | wajib | wajib |
| `AI_RO_PASSWORD` atau `ANALYTICS_PG_DSN` penuh | wajib bila SQL tool dipakai | wajib |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | bebas | wajib (on-prem) |
| `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `EMBEDDING_SEND_DIMENSIONS` | bebas | wajib |
| `BE_BASE_URL_DOCKER` (dev) / `BE_BASE_URL` (prod) | default container BE | default nginx internal |
| `AI_PORT` / `AI_PORT_RANGE`, `AI_REPLICAS`, `AI_MEM_LIMIT` | opsional | opsional |

Variabel yang tidak terdaftar di blok `environment:` compose **tidak** sampai ke container.

## 4. Menjalankan

```bash
# dev
docker compose --env-file .env -f docker/docker-compose.dev.ai-postgres.yaml up -d
docker compose --env-file .env -f docker/docker-compose.dev.ai-service.yaml up -d --build
curl http://localhost:3040/readyz
# prod
docker compose --env-file .env -f docker/docker-compose.prod.ai-postgres.yaml up -d
docker compose --env-file .env -f docker/docker-compose.prod.ai-service.yaml pull
docker compose --env-file .env -f docker/docker-compose.prod.ai-service.yaml up -d
curl http://localhost:3006/readyz && curl http://localhost:3007/readyz
```

Migrasi DB sendiri berjalan otomatis saat boot (`AUTO_MIGRATE=true`, aman multi-replica via
`pg_advisory_lock`). Unggah dokumen awal: `docker exec ssp2_dev_ai ai-service ingest /data/uploads/<berkas>`
atau lewat `POST /api/v1/admin/ingest` (SSH tunnel ke port host).

## 5. nginx

Tempel `deploy/nginx/ai.conf.snippet` ke `turborepo/docker/nginx/default.conf`: upstream `ssp2_ai`,
blok SSE `/ai/api/v1/chat/stream` (tanpa buffering, timeout 15 m), 403 untuk `/ai/api/v1/internal`
dan `/ai/api/v1/admin`, `location /ai/` (prefix dihapus), dan `location /ai/` di listener internal `:8080`.

## 6. Verifikasi

1. `curl /readyz` → `db: ok`, `analytics_db: ok`, `llm: configured`.
2. `./scripts/smoke.sh` dari server (`BASE_URL=http://localhost:3040`, `SMOKE_NO_MOCK_BE=1` bila BE nyata).
3. Login FE → `GET /ai/api/v1/me` lewat BFF → `ai_role`/`scope` sesuai akun.
4. `docker exec ssp2_prod_ai ai-service catalog drift` → semua view OK terhadap DB BE.
5. Pantau `GET /api/v1/admin/sql-audit` dan log JSON (`latency_ms`) terhadap target < 5 s (RAG) / < 10 s (SQL).

## 7. Operasional

- **Rollback**: `export IMAGE_TAG=v<lama> && docker compose … up -d`.
- **Scaling**: ubah `AI_REPLICAS` + `AI_PORT_RANGE` (jumlah port = replica) dan upstream nginx.
- **Rotasi key**: `API_KEYS`/`ADMIN_API_KEYS` menerima daftar koma — tambah key baru, deploy, ganti di klien, hapus key lama.
- **Backup**: `docker exec ssp2_prod_ai_postgres pg_dump -U postgres ai_service > ai_service-$(date +%F).sql`
  (berkas asli unggahan ada di volume `ai-prod-uploads`).
- **Ganti model embedding**: ubah `EMBEDDING_MODEL`/`EMBEDDING_DIM` → service menolak boot →
  `docker compose run --rm ai ai-service reindex --confirm` → `up -d`.
- **Dokumen baru/berubah**: unggah ulang dengan `replace=true` (sha256 berbeda otomatis dianggap dokumen baru).
- **Skema BE berubah**: `ai-service catalog drift`; perbarui `scripts/gen_catalog.py`, jalankan
  `uv run python scripts/gen_catalog.py`, rilis.
- **Log**: JSON ke stdout (`docker logs`), field `request_id`, `session_id`, `intent`, `latency_ms`, `tools`.

## 8. Troubleshooting

| Gejala | Penyebab umum |
|---|---|
| `/readyz` `analytics_db: down` | `ANALYTICS_PG_DSN` salah / grant belum dijalankan / pg_hba |
| Semua pengguna `public`, log "BE tidak tersedia" | `BE_BASE_URL` salah, `JWT_SECRET` beda, throttler BE 429 |
| Boot gagal "konfigurasi embedding berubah" | model/dimensi embedding diganti tanpa `reindex --confirm` |
| `sql.status = rejected` berulang | model LLM lemah dalam tool calling / dialek; coba `LLM_SQL_MODEL` lebih kuat |
| Jawaban "tidak ditemukan" padahal dokumen ada | cek `POST /admin/search`; chunk terlalu besar, ganti `RAG_CHUNK_SIZE` lalu `reindex` |
| 429 pada chat | `RATE_LIMIT_*` per replica; naikkan atau tambah replica |
