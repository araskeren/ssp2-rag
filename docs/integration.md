# Integrasi ai-service dengan FE, BE, dan konektor pesan

## 1. Frontend web (turborepo/apps/fe)

Token pengguna tersimpan di cookie httpOnly, jadi FE memanggil ai-service lewat **BFF** seperti
`src/routes/api/notif/$.ts` untuk notification-service:

1. Buat `src/routes/api/ai/$.ts` yang meneruskan `/api/ai/*` → `${AI_URL}/api/v1/*` sambil
   menyisipkan `Authorization: Bearer <accessToken cookie>` dan `X-Forwarded-For` klien.
2. Env FE: `AI_URL` per environment (local compose `http://ssp2_dev_ai:3000`, prod
   `http://ssp2_prod_ai:3000` atau lewat nginx internal). Daftarkan di allowlist compose FE.
3. Streaming: `fetch('/api/ai/chat/stream', {method: 'POST', body})` lalu baca
   `response.body` (ReadableStream) dan parse blok `event:`/`data:`; BFF meneruskan `upstream.body`
   apa adanya (pola proxy SSE inbox).
4. Gate UI mode "Analisis Data" dengan `GET /api/ai/me` → `ai_role === 'analyst'`; tampilkan
   `scope_label` agar pengguna tahu batas wilayah data.
5. Tampilkan `sources[]` sebagai daftar sitasi (ref, judul/berkas, halaman) dan `sql` sebagai
   tabel + SQL yang bisa disalin.

## 2. Konektor WhatsApp = notification-service

Kanal WhatsApp **dimiliki notification-service** (repo `ssp2-notificcation`); ai-service hanya
"otak" AI. Panduan lengkap sisi konektor: `ssp2-notificcation/docs/whatsapp-chat.md`.

```
WhatsApp ⇄ wa-gateway (Baileys, sidecar notification) → notification-service (Go)
   simpan + dedup per message_id → gabung pesan beruntun per nomor →
   POST {AI_SERVICE_URL}/api/v1/internal/chat
     header: X-API-Key: <salah satu API_KEYS ai-service>, X-Request-ID: <id pesan masuk>
     body:   {"platform": "wa", "channel_id": "628…", "query": "<teks gabungan>", "display_name": "<nama>"}
   ← {"answer", "text_plain", "intent", "sources", "sql", "identity": {"ai_role", "scope_level", …}, …}
   → envelope notifikasi biasa {"source": "wa-chat", "event": "chat.reply",
       "idempotency_key": "wa-chat:<provider>:<message id pertama>", "body": "<text_plain>",
       "channels": {"whatsapp": {"to": ["+628…"], "text": "<text_plain>"}}} → dispatcher → WhatsApp
```

Yang perlu disiapkan di ai-service:

| Item | Nilai |
|---|---|
| `API_KEYS` | tambahkan `AI_SERVICE_API_KEY` milik notification-service (daftar koma) |
| URL dari notification | `http://ssp2_dev_ai:3000` (dev/local, network `ssp2-dev-net` / `ssp2-local-net`), `http://ssp2_prod_ai:3000` (prod, alias semua replika) |
| Flag `connector` | harus menyala (`GET/PUT /api/v1/admin/features`); mati → 503 → notification membalas "layanan tidak tersedia" |
| `allowed_channels` | opsional: nomor analis (`channel add wa 628… --role analyst --scope …`); nomor lain → `public` |
| `CHAT_MAX_QUERY_CHARS` | samakan dengan `AI_CHAT_MAX_QUERY_CHARS` notification (default 2000) |

Perilaku yang diandalkan konektor:
- `text_plain` sudah tanpa markdown tabel/heading dan ≤ 4000 karakter (batas `body` gateway).
- ai-service **tidak** mendeduplikasi; idempotensi per pesan masuk ditangani notification-service.
- Timeout klien 120 s (`CHAT_TIMEOUT_S` 90 s + jaringan). Pada 429/503/504/403/400 konektor
  mengirim balasan baku tanpa retry; hanya gangguan jaringan / 5xx lain yang di-retry.
- Limit per IP tidak berlaku di `/internal/chat` (semua pengguna lewat IP konektor); limit per nomor
  (`RATE_LIMIT_CHAT_PER_MIN`) tetap berlaku.
- Kanal yang boleh memakai analisis data didaftarkan admin di `allowed_channels`
  (`ai-service channel add wa 628… --role analyst --scope PROVINCE --provinsi-kode 33`).
  Nomor disimpan dalam bentuk digit E.164 (`628…`).
- Endpoint internal tidak diekspos nginx publik (403); konektor memakai DNS container langsung
  atau listener internal `:8080` (`http://ssp2_prod_nginx:8080/ai/api/v1/internal/chat`).
- Telegram (`platform: "tg"`) tetap didukung kontraknya; konektornya belum dibuat.

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
