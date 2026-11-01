# ai-service — Asisten AI Satu Data Penyakit

Layanan Python (FastAPI + LangChain) yang menyediakan **asisten helpdesk berbasis dokumen (RAG)**
untuk semua pengguna dan **analisis data operasional (Text-to-SQL, read-only)** khusus pengguna
berwenang, dengan otorisasi tingkat tool (RBAC) dan pembatasan wilayah mengikuti aturan backend.

**Kanal:** web (JWT backend) · konektor WhatsApp/Telegram (API internal ber-`X-API-Key`; pengiriman
balasan tetap lewat `notification-service`). **Stack:** Python 3.12 · FastAPI · LangChain v1 ·
PostgreSQL 18 + pgvector · LLM OpenAI-compatible (cloud untuk dev, vLLM/Ollama on-prem untuk prod).

## Arsitektur

```
Web FE (BFF /api/ai/* → Bearer JWT BE) ─┐                         ┌─ LLM OpenAI-compatible (LLM_BASE_URL)
Konektor WA/TG (X-API-Key, nanti) ──────┴→ ai-service (FastAPI) ──┼─ BE GET /auth/profile → role, permission, faskes
                                           │ LangChain agent        ├─ ai_postgres (pgvector): dokumen, chunk, sesi, audit, kanal
                                           │  ├ cari_dokumen        └─ Analitik (pluggable): Postgres BE via role ai_ro
                                           │  └ analisis_data (analis)   ClickHouse dn_* via app_ro (opsional)
                                           └ middleware: prompt dinamis + otorisasi tool (deterministik, diaudit)
```

- **Identitas web**: JWT HS256 diverifikasi lokal (`JWT_SECRET` sama dengan BE), lalu profil
  diambil dari `GET {BE_BASE_URL}/auth/profile` (cache per sesi). `ai_role` = `analyst` bila nama
  role ∈ `AI_ANALYST_ROLES`, atau memiliki permission `AI_ANALYST_PERMISSION`, atau terdaftar di
  `allowed_channels(web, <user id>)`. Scope wilayah (GLOBAL/PROVINCE/KABKOTA/FASKES) di-port 1:1
  dari `deriveUserScope` BE dan **disuntikkan oleh kode** ke setiap query analitik.
- **Identitas konektor** (wa/tg): tabel `allowed_channels` → role + scope; kanal tak terdaftar
  mengikuti `INTERNAL_UNKNOWN_CHANNEL_POLICY` (public | deny).
- **RAG**: unggah PDF/TXT/MD/DOCX → potong (RecursiveCharacterTextSplitter) → embedding → pgvector;
  retrieval hybrid (cosine HNSW ⊕ full-text Indonesia + token mentah, fusi RRF) → jawaban bersitasi `[n]`.
- **Text-to-SQL**: LLM hanya melihat *virtual view* katalog (`src/ai_service/sql/catalog/*.yaml`,
  porting `dn_*` data-pipeline tanpa kolom PII) → validator sqlglot (satu SELECT, hanya view katalog,
  LIMIT diklem, denylist fungsi) → dibungkus CTE + filter scope ber-parameter → dieksekusi sebagai
  role read-only `ai_ro` (READ ONLY, `statement_timeout`) → koreksi otomatis ≤ `SQL_MAX_RETRIES` →
  **setiap percobaan dicatat di `sql_audit_log`**. Pengguna public yang meminta data dihentikan di
  middleware tool (bukan oleh LLM) dan dicatat `access_denied`.

## Docker Compose — 3 environment (local · dev · prod)

| Env | File | Keterangan |
|---|---|---|
| **local** | `docker/docker-compose.local.yaml` | service + Postgres pgvector (DB `ai_service` + fixture `sdp_fixture` + role `ai_ro`) dalam satu file; network sendiri |
| **dev** | `docker-compose.dev.ai-service.yaml`<br>`docker-compose.dev.ai-postgres.yaml` | per komponen; join network bersama turborepo `ssp2-dev-net`; host port 3040 (service) / 3150 (DB) |
| **prod** | `docker-compose.prod.ai-service.yaml`<br>`docker-compose.prod.ai-postgres.yaml` | image dari registry (tanpa build), replica 2, port host `3006-3007` untuk upstream nginx, alias `ssp2_prod_ai`; rahasia wajib diisi |

### Local — laptop, satu perintah

```bash
cp .env.example .env            # isi LLM_API_KEY (atau arahkan LLM_BASE_URL ke Ollama), samakan JWT_SECRET dengan BE
docker compose --env-file .env -f docker/docker-compose.local.yaml up -d --build
curl http://localhost:3040/readyz   # {"db":"ok","analytics_db":"ok","llm":"configured"}
./scripts/smoke.sh                  # end-to-end (SMOKE_SKIP_LLM=1 untuk tanpa LLM)
```

Akses dari host: API `localhost:3040` (Swagger `/docs`) · Postgres `localhost:3150`
(`ai_service`, `sdp_fixture`). BE native di host dijangkau container via `host.docker.internal:3001`;
untuk dev tanpa BE jalankan `uv run python scripts/mock_be.py --port 3001`.

### Dev / Prod

Lihat [docs/deployment.md](docs/deployment.md) — termasuk tabel `.env`, nginx (`/ai/`),
skrip grant `ai_ro` pada DB BE, dan runbook operasional.

## Menjalankan native (tanpa Docker)

```bash
uv sync                                     # Python 3.12 otomatis (uv)
cp .env.example .env                        # default menunjuk Postgres lokal 127.0.0.1:5432
createdb ai_service && psql -d ai_service -c 'CREATE EXTENSION vector'
uv run ai-service migrate                   # alembic upgrade head
uv run ai-service serve                     # http://localhost:3003 (Swagger /docs)
uv run ai-service ingest docs/samples/panduan-pelaporan-sari.md
```

Uji dengan LLM lokal tanpa kunci cloud: `ollama pull qwen2.5:3b nomic-embed-text`, jalankan
`OLLAMA_CONTEXT_LENGTH=16384 ollama serve`, lalu `set -a; source .env.ollama; set +a; uv run ai-service serve`
(model 3B cukup untuk smoke test; produksi butuh model yang lebih kuat).

Fixture analitik lokal (opsional): `createdb sdp_fixture` lalu muat
`tests/fixtures/be_schema_min.sql`, `tests/fixtures/be_fixture_data.sql`, dan
`scripts/grant-ai-ro.sql` (`-v ai_ro_password=ai_ro -v DBNAME=sdp_fixture`).

## Konfigurasi

Semua lewat environment variable — lihat [.env.example](.env.example) yang berkomentar lengkap.
Wajib di prod: `JWT_SECRET` (= BE), `API_KEYS`, `ADMIN_API_KEYS`, `LLM_BASE_URL`, `LLM_MODEL`,
`EMBEDDING_MODEL`, `EMBEDDING_DIM`, `AI_DATABASE_URL`/`AI_PG_PASSWORD`, `ANALYTICS_PG_DSN`/`AI_RO_PASSWORD`.
Mengganti model/dimensi embedding setelah ada data mewajibkan `ai-service reindex --confirm`
(service menolak start bila konfigurasi tidak cocok dengan data tersimpan).

**Multi-provider LLM.** Selain `LLM_*` (fallback), admin dapat mendaftarkan banyak provider
`openai_compatible` / `anthropic_compatible` — masing-masing dengan `base_url` + API key sendiri
(terenkripsi dengan `AI_SECRETS_KEY`) — lalu menentukan model per keperluan (chat, SQL, judge, embedding),
per role (`public`/`analyst`), atau per kanal/user lewat `/api/v1/admin/llm/*`
(lihat [docs/api.md](docs/api.md#admin--registry-llm-x-api-key-admin)). `ai-service llm import-env`
menyalin konfigurasi env saat ini ke registry.

## API singkat

| Endpoint | Auth | Fungsi |
|---|---|---|
| `POST /api/v1/chat` · `POST /api/v1/chat/stream` (SSE) | Bearer JWT | chat web; respons memuat `intent`, `sources[]`, `sql` |
| `GET /api/v1/me` | Bearer JWT | `ai_role` + scope (FE memakainya untuk menampilkan mode Analisis) |
| `GET/DELETE /api/v1/chat/sessions…` | Bearer JWT | daftar/riwayat/hapus sesi milik sendiri |
| `POST /api/v1/internal/chat` | `X-API-Key` | chat untuk konektor WA/TG (`platform`, `channel_id`) + `text_plain` |
| `POST /api/v1/admin/ingest` · `/admin/documents…` · `/admin/search` | `X-API-Key` admin | ingest & kelola dokumen, debug retrieval |
| `GET/POST/PATCH/DELETE /api/v1/admin/channels` | `X-API-Key` admin | kelola `allowed_channels` (+ override model per kanal) |
| `/api/v1/admin/llm/providers…` · `/models…` · `/assignments` · `/resolve` | `X-API-Key` admin | registry provider LLM (OpenAI-/Anthropic-compatible) & penugasan model |
| `GET /api/v1/admin/sql-audit` | `X-API-Key` admin | audit setiap percobaan SQL (termasuk `access_denied`) |
| `GET /api/v1/admin/catalog` | `X-API-Key` admin | katalog virtual view Text-to-SQL (+ `?prompt=true`) |
| `GET /healthz` · `GET /readyz` | — | probe |
| `GET /docs` | — | Swagger UI (hanya `APP_ENV=local\|dev`) |

Detail: [docs/api.md](docs/api.md). Kontrak integrasi FE/BE/konektor: [docs/integration.md](docs/integration.md).

## Perintah CLI

```bash
uv run ai-service migrate                      # migrasi DB sendiri
uv run ai-service ingest <berkas...> [--tags]  # indeks dokumen
uv run ai-service reindex --confirm            # potong+embed ulang (ganti model embedding)
uv run ai-service channel add wa 628123456789 --role analyst --scope PROVINCE --provinsi-kode 33
uv run ai-service catalog check|show|drift     # validasi katalog, lihat prompt skema, cek drift skema BE
uv run ai-service eval rag|sql|access|judge    # evaluasi (laporan di eval/reports/)
uv run ai-service llm gen-key|list|import-env  # kunci AI_SECRETS_KEY, lihat registry LLM, salin LLM_* ke registry
```

## Test & lint

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                                  # unit saja
AI_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/ai_service_test uv run pytest   # + integrasi Postgres
```

Tes integrasi memuat fixture BE ke DB `sdp_fixture` (`AI_TEST_ANALYTICS_ADMIN_DSN`) dan memakai
model chat/embedding palsu — tidak memanggil LLM nyata.

## Evaluasi

```bash
uv run ai-service eval rag        # recall@k / MRR retrieval (tanpa LLM)
uv run ai-service eval access     # zero-leakage: public harus ditolak & diaudit (LLM)
uv run ai-service eval sql        # validity / exact-result / reject rate / kebocoran scope vs fixture (LLM)
uv run ai-service eval judge      # faithfulness & cakupan poin kunci via LLM-judge (LLM)
```

Golden set ada di `eval/*.jsonl`, laporan JSON di `eval/reports/`. Angka `access`, `sql`, dan
`judge` sangat bergantung pada model: dengan model stand-in 3B lokal, retrieval sudah sempurna
(recall@5 = 1.0) dan tidak pernah ada SQL yang dieksekusi pengguna public, tetapi validitas SQL
dan sitasi rendah. Gunakan model produksi (Qwen 14B+ / gpt-4o-mini) dan model judge yang lebih
kuat sebelum membaca angka kualitasnya sebagai tolok ukur.

## Batasan yang disadari

- **Lag revokasi sesi** ≤ `PROFILE_CACHE_TTL_S` (default 5 menit); saat BE tidak terjangkau,
  pengguna dengan token sah diperlakukan `public` (fail-closed untuk SQL, helpdesk tetap jalan).
- **Throttler BE per IP**: ai-service meneruskan IP pengguna (`X-Forwarded-For`), namun
  `GET /auth/profile` sebaiknya diberi `@SkipThrottle()`/limit lebih longgar di BE.
- **Rate limit & job ingest per replica** (in-process, tanpa Redis); volume unggahan dibagi antar replica di satu host.
- **Privasi LLM cloud**: potongan dokumen dan hasil agregat dikirim ke `LLM_BASE_URL` / provider registry yang ditugaskan; produksi wajib on-prem.
- **Katalog = porting manual** skema BE; jalankan `catalog drift` setelah migrasi BE.
- **ClickHouse** (`SQL_BACKEND=clickhouse`) menunggu regenerasi pipeline ke skema dev dan user `app_ro` yang dipersempit.

## Struktur repo

```
src/ai_service/   api/ auth/ agent/ rag/ sql/ db/  (lihat docs/)
docker/           compose local/dev/prod + init Postgres
deploy/           gitlab-ci.yml, nginx/ai.conf.snippet
scripts/          smoke.sh, mock_be.py, mint_jwt.py, grant-ai-ro.sql, gen_catalog.py, check-catalog-drift.sh
tests/            unit, api, integration (Postgres), fixtures (subset skema BE)
eval/             golden set + laporan
docs/             api.md, integration.md, deployment.md, samples/
```
