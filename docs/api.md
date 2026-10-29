# API ai-service

Base URL: `http://<host>:<port>` (container 3000; local/dev host 3040; prod lewat nginx `/ai/`).
Semua respons JSON; error memakai envelope ala NestJS:

```json
{"statusCode": 400, "error": "Bad Request", "message": "validasi request gagal", "details": ["query: wajib diisi"]}
```

## Autentikasi

| Skema | Header | Dipakai oleh |
|---|---|---|
| Bearer JWT BE (HS256, `JWT_SECRET` sama dengan backend) | `Authorization: Bearer <access token>` | endpoint web (`/chat*`, `/me`) |
| API key konektor (`API_KEYS`) | `X-API-Key` | `/internal/chat` |
| API key admin (`ADMIN_API_KEYS`) | `X-API-Key` | `/admin/*` |

Token sementara 2FA (`isTemp`) dan token tanpa `sessionId` ditolak (401). Sesi yang dicabut/diblokir
di BE menghasilkan 401 selama profil belum ter-cache (TTL `PROFILE_CACHE_TTL_S`).

## Chat web

### `POST /api/v1/chat`

Request:
```json
{"session_id": "web_3f1c…", "query": "Bagaimana alur pelaporan kasus SARI?"}
```
`session_id` opsional (format `web_<uuid>`); bila kosong server membuatnya. Sesi hanya dapat dipakai
pemilik (403 bila bukan). Batas `query` = `CHAT_MAX_QUERY_CHARS`.

Response 200:
```json
{
  "session_id": "web_3f1c…", "message_id": "019a…",
  "intent": "helpdesk | data_analysis | denied | smalltalk",
  "answer": "Alur dimulai dari pendaftaran kasus … [1]",
  "sources": [{"ref": "[1]", "document_id": "…", "filename": "juknis-sari.pdf", "title": "…", "page": 3, "section": "2. Pendaftaran kasus", "snippet": "…", "score": 0.0325}],
  "sql": {"backend": "postgres", "status": "ok", "query": "SELECT …", "views": ["dn_sari_surveilans"], "columns": ["provinsi", "jumlah_kasus"], "rows": [["Jawa Tengah", 120]], "row_count": 34, "truncated": false, "scope_label": "Nasional (GLOBAL)", "elapsed_ms": 212, "attempts": 1},
  "usage": {"prompt_tokens": 1830, "completion_tokens": 96}, "latency_ms": 2410,
  "tool_calls": [{"name": "cari_dokumen", "status": "ok", "summary": "6 potongan dari 2 dokumen", "elapsed_ms": 180}]
}
```
`sql` = `null` bila tidak ada analisis; `sql.status` ∈ `ok | empty | rejected | error | timeout | unavailable`.
`sql.query` adalah SQL buatan LLM (CTE view yang diperluas hanya tersimpan di audit).

Kode: 400 validasi · 401 token · 403 sesi bukan milik · 429 rate limit (`Retry-After: 60`) ·
503 asisten belum siap · 504 LLM tidak merespons.

### `POST /api/v1/chat/stream` — Server-Sent Events

Request sama. Response `text/event-stream` (header `Cache-Control: no-cache`, `X-Accel-Buffering: no`),
tiap event `event: <nama>\ndata: <json>\n\n`:

| event | data |
|---|---|
| `meta` | `{session_id, message_id, ai_role}` (pertama) |
| `tool` | `{name: "cari_dokumen"\|"analisis_data", status: "start"\|"end", summary}` |
| `token` | `{t: "…"}` potongan teks jawaban |
| `sources` | `{items: [...]}` (sama dengan `sources` di atas) |
| `sql` | objek `sql` di atas |
| `done` | `{message_id, intent, usage, latency_ms}` (terakhir) |
| `error` | `{statusCode, message}` lalu koneksi ditutup |
| `ping` | `{}` tiap `SSE_HEARTBEAT_S` detik bila tidak ada event |

Klien browser memakai `fetch` + `ReadableStream` (bukan `EventSource`, karena metode POST + header).

### `GET /api/v1/me`
```json
{"user_id": "42", "email": "…", "display_name": "…", "role_name": "Admin Kemenkes", "ai_role": "analyst",
 "analyst_reason": "role:Admin Kemenkes", "scope": {"level": "GLOBAL"}, "scope_label": "Nasional (GLOBAL)"}
```
`scope.level` ∈ `GLOBAL | PROVINCE (provinsi_kode) | KABKOTA (kabupaten_kota_kode) | FASKES (facility_id)`.

### Sesi
- `GET /api/v1/chat/sessions?limit=` → `{items:[{session_id,title,message_count,created_at,last_message_at}]}`
- `GET /api/v1/chat/sessions/{id}/messages?limit=` → `{session_id, items:[{id,role,content,intent,sources,sql,created_at}]}`
- `DELETE /api/v1/chat/sessions/{id}` → 204

## Internal (konektor WA/TG)

### `POST /api/v1/internal/chat` (`X-API-Key`)
```json
{"platform": "wa", "channel_id": "+62 812-3456-789", "query": "Apa definisi SARI?", "display_name": "Budi"}
```
`channel_id` dinormalkan (WA → digit E.164 tanpa `+`, awalan `0` → `62`; TG → string chat id).
`session_id` = `wa_<digit>` / `tg_<chat id>` (satu sesi berkelanjutan per kanal).
Response = `ChatResponse` + `text_plain` (markdown → teks WA/TG + daftar sumber, ≤ 4000 karakter)
+ `identity {ai_role, scope_level, scope_label, known_channel}`. Kanal tak terdaftar: `public`
(atau 403 bila `INTERNAL_UNKNOWN_CHANNEL_POLICY=deny`).

## Admin (`X-API-Key` admin)

| Endpoint | Fungsi |
|---|---|
| `POST /api/v1/admin/ingest` (multipart `files[]`, `title?`, `source?`, `tags?` csv, `replace?`, `sync?`) | 202 `{job_id, status, items:[{filename, document_id, status: queued\|indexed\|duplicate\|failed}]}`; sha256 duplikat = `duplicate` (idempoten) |
| `GET /api/v1/admin/ingest/{job_id}` | status job |
| `GET /api/v1/admin/documents?status=&q=&page=&limit=` · `GET /documents/{id}` · `DELETE /documents/{id}` · `POST /documents/{id}/reindex` | kelola dokumen |
| `POST /api/v1/admin/search` `{query, top_k}` | debug retrieval (hit + dense/sparse rank) |
| `GET/POST /api/v1/admin/channels` · `PATCH/DELETE /channels/{id}` | `allowed_channels` (`platform`, `channel_id`, `role`, `scope_level`, `provinsi_kode`, `kabupaten_kota_kode`, `facility_id`, `is_active`) |
| `GET /api/v1/admin/sql-audit?status=&principal_id=&from=&to=&page=&limit=` | audit SQL: `ok\|rejected\|error\|timeout\|access_denied`, SQL yang dibuat & dieksekusi, view, percobaan, durasi |
| `GET /api/v1/admin/catalog?prompt=` | katalog virtual view (kolom, scope, contoh) + hasil validasi; `prompt=true` menyertakan rendering untuk LLM |

## Probe
`GET /healthz` → `ok`. `GET /readyz` → `{"db": "ok|down", "analytics_db": "ok|down|skipped", "llm": "configured|missing"}` (503 hanya bila DB sendiri down).
