# Integrasi ai-service dengan FE, BE, dan konektor pesan

## 1. Frontend web (chatbot, `fe/`)

FE standalone di `fe/` memakai **Vite SPA murni** (Vite + React 19 + TypeScript). Tidak ada SSR
dan tidak ada BFF: browser memanggil ai-service langsung, mengirim JWT sebagai Bearer.

1. **Stack**: `vite` (build), React 19, TypeScript. Entry `fe/src/main.tsx`, komponen `fe/src/App.tsx`.
2. **Auth** — dua mode:
   - *public*: pengguna tanpa login, `identity` jatuh ke kebijakan publik ai-service.
   - *auth BE*: login ke BE (`POST {BE_URL}/auth/login`) → JWT disimpan di `localStorage`
     (`access_token`), lalu dikirim sebagai `Authorization: Bearer <jwt>`. Validasi JWT
     sepenuhnya di BE/ai-service — FE tidak memutuskan peran.
3. **Env FE** (`fe/src/lib/env.ts`): `VITE_AI_URL` (default `http://localhost:3003/api/v1`),
   `VITE_BE_URL` (default `http://localhost:3001`).
4. **CORS wajib**: karena tanpa BFF, ai-service harus mengizinkan origin FE —
   set `CORS_ORIGIN=http://localhost:5173` di `.env` ai-service dan `CORS_ORIGIN` di mock BE.
5. **Streaming SSE**: `fetch('/chat/stream')` → baca `response.body` (ReadableStream), parse blok
   `event:`/`data:` (event `meta`, `token`, `tool`, `sources`, `sql`, `done`, `error`).
6. **Riwayat sesi**: `GET /chat/sessions` (sidebar), `GET /chat/sessions/{id}/messages` (detail),
   `DELETE /chat/sessions/{id}` (hapus).
7. **Gate mode Analisis**: `GET /me` → `ai_role === 'analyst'`; tampilkan `scope_label` agar
   pengguna tahu batas wilayah data. Penegakan tetap di server (RBAC + permission `ai_chat:analyze`).
8. **Tampilan hasil**: `sources[]` sebagai daftar sitasi (ref, judul/berkas, halaman); `sql`
   dirender sebagai **tabel hasil saja** (query SQL internal, tidak ditampilkan).

### Menjalankan lokal (3 terminal)

```bash
uv run ai-service serve                        # :3003
uv run python scripts/mock_be.py --port 3001   # :3001
cd fe && npm run dev                           # :5173
```

## 2. Kontrak konektor WhatsApp/Telegram (notification-service atau layanan penerus)

notification-service saat ini **outbound-only**. Komponen yang menerima pesan masuk (webhook
Telegram, Baileys/Meta untuk WA) memanggil ai-service lalu mengirim balasan lewat jalur
notification-service biasa:

```
pesan masuk → POST {AI_URL}/api/v1/internal/chat
  header: X-API-Key: <API_KEYS ai-service>, Content-Type: application/json
  body:   {"platform": "wa"|"tg", "channel_id": "<nomor E.164 tanpa +  |  chat id>", "query": "<teks>", "display_name": "<nama>"}
respons → {"answer", "text_plain", "sources", "sql", "identity": {"ai_role", "scope_level", "known_channel"}, ...}
balasan → envelope notification-service:
  {"source": "ai-connector", "event": "chat.reply", "idempotency_key": "<platform>:<message id provider>",
   "title": "Asisten Satu Data Penyakit", "body": "<text_plain>",
   "channels": {"whatsapp": {"to": ["+628…"]}}  atau  {"telegram": {"chat_ids": ["<chat id>"]}}}
```

Aturan:
- `text_plain` sudah tanpa markdown tabel/heading dan ≤ 4000 karakter (batas `body` gateway); bila
  perlu pecah menjadi beberapa pesan.
- Idempoten per pesan masuk (pakai id pesan provider sebagai kunci; ai-service sendiri tidak
  mendeduplikasi).
- Timeout panggilan 120 s (`CHAT_TIMEOUT_S` 90 s + jaringan). Pada 429/503/504 kirim balasan
  permintaan maaf baku, jangan retry agresif.
- Kanal yang boleh memakai analisis data didaftarkan admin di `allowed_channels`
  (`ai-service channel add wa 628… --role analyst --scope PROVINCE --provinsi-kode 33`).
  Nomor disimpan dalam bentuk digit E.164 (`628…`).
- Endpoint internal tidak diekspos nginx publik (403); konektor memakai listener internal `:8080`
  (`http://ssp2_prod_nginx:8080/ai/api/v1/internal/chat`) atau DNS container langsung.

## 3. Backend NestJS (turborepo/apps/be)

- **JWT**: `JWT_SECRET` ai-service harus identik; ai-service hanya membaca `sub`, `email`,
  `sessionId`, menolak `isTemp`.
- **Profil**: `GET /auth/profile` dipanggil per sesi (cache 5 menit) dengan `X-Forwarded-For` IP
  pengguna. Karena throttler BE global per IP, pertimbangkan `@SkipThrottle()` pada endpoint ini.
- **Akses analis**: permission `ai_chat:analyze` (modul BE "Asisten AI"), diatur per role di
  Manajemen › Role. `AI_ANALYST_ROLES` kosong secara default; isi hanya untuk pengecualian eksplisit.
- **Menu Manajemen › AI**: BE memanggil API admin lewat `AI_SERVICE_URL` + `AI_SERVICE_ADMIN_KEY` (salah satu `ADMIN_API_KEYS`).
  Sakelar `assistant` mengikuti `master.modul` baris `asisten-ai`: BE mendorong ke ai-service lebih dulu, bila gagal modul tidak berubah.
  Deploy ai-service sebelum BE. Menyimpan API key provider dari menu butuh `AI_SECRETS_KEY`.
- **Grant DB (DBA, sekali + setiap ada tabel baru)**:
  ```bash
  psql -h <host-pg-be> -U postgres -d satu_data_penyakit -v ai_ro_password='<rahasia>' -v DBNAME=satu_data_penyakit -f scripts/grant-ai-ro.sql
  ```
  Role `ai_ro`: SELECT hanya pada tabel yang dipakai katalog; `master.patients` column-level tanpa
  PII; tanpa akses `master.users`; `default_transaction_read_only=on`, `statement_timeout=15s`.
  Setelah migrasi BE jalankan `./scripts/check-catalog-drift.sh` dan perbarui
  `scripts/gen_catalog.py` bila ada kolom yang berubah.

## 4. Alur admin `allowed_channels`

| Kasus | Perintah / request |
|---|---|
| Analis WA nasional | `channel add wa 628123456789 --role analyst --scope GLOBAL` |
| Analis Telegram provinsi | `channel add tg -1001234 --role analyst --scope PROVINCE --provinsi-kode 33` |
| Pengguna web diberi/dicabut analis tanpa ubah role BE | `channel add web <user id> --role analyst\|public [--scope …]` |
| Nonaktifkan sementara | `PATCH /api/v1/admin/channels/{id} {"is_active": false}` |

Baris `web` dengan scope ≠ GLOBAL **mempersempit** scope turunan profil BE.

## 5. ClickHouse (tahap berikutnya)

Set `SQL_BACKEND=clickhouse` + `ANALYTICS_CH_*` dan pasang extra `uv sync --extra clickhouse`.
Prasyarat: pipeline data-pipeline diregenerasi terhadap skema dev (id uuid, schema `kesling_*`) dan
user `app_ro` dibatasi ke database view saja. Katalog memakai SQL dialek `clickhouse` yang sudah ada
di tiap YAML (`SELECT * EXCEPT (patient_name, patient_birth_date) FROM satu_data_penyakit.dn_*`).
