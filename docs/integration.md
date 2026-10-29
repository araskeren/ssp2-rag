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
- **Role analis**: default `AI_ANALYST_ROLES` (superadmin, Kemenkes, Eksekutif) dan permission
  `menu_epi_intel:ai`. Bila BE menambah role/permission khusus (mis. `ai_chat:analyze`), cukup ubah env.
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
