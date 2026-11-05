# FE — Asisten Satu Data Penyakit (standalone dev)

Frontend chat mandiri untuk menguji `ai-service` lokal. Stack: **Vite + React 19 + TypeScript**
(SPA murni, tanpa SSR dan tanpa BFF).

```text
Browser ──► FE Vite (:5173) ──► ai-service (:3003/api/v1)   Bearer JWT
                            └─► mock BE     (:3001/auth/*)  dev-only login
```

Browser memanggil backend langsung, jadi **CORS wajib aktif**:

- ai-service: `CORS_ORIGIN=http://localhost:5173` di `.env`
- mock BE: env `CORS_ORIGIN` (default `http://localhost:5173`)

## Jalankan (3 terminal)

Prasyarat: `ai-service` (:3003) dan mock BE (:3001) menyala.

```bash
# terminal 1
uv run ai-service serve

# terminal 2
uv run python scripts/mock_be.py --port 3001

# terminal 3
cd fe && npm install && npm run dev    # http://localhost:5173
```

Login halaman depan = **mode dev saja** (memilih persona mock-BE: analis GLOBAL,
analyst KABKOTA, public PROVINCE, dsb.) untuk melihat efek RBAC & scope. JWT hasil
login disimpan di `localStorage` (`access_token`) dan dikirim sebagai `Authorization: Bearer`.

## Env (opsional)

| Variabel       | Default                        |
|----------------|--------------------------------|
| `VITE_AI_URL`  | `http://localhost:3003/api/v1` |
| `VITE_BE_URL`  | `http://localhost:3001`        |

## Struktur

```text
fe/
├─ index.html          entry HTML (font + #root)
├─ vite.config.ts      plugin react + alias "~" → /src
└─ src/
   ├─ main.tsx         bootstrap React
   ├─ App.tsx          login + chat + sidebar
   ├─ styles.css       token desain SATUSEHAT
   └─ lib/
      ├─ env.ts        konfigurasi URL
      └─ api.ts        klien API (login, me, sesi, stream)
```

## Cakupan v1

- Auth dua mode: public (tanpa login) & auth BE (JWT tervalidasi di server)
- Chat streaming SSE (`POST /chat/stream`, `fetch` + `ReadableStream`)
- Sitasi (`sources`) + tabel hasil SQL (query SQL internal, tidak ditampilkan)
- Riwayat sesi: daftar, detail (buka sesi lama), hapus
- Gate mode Analisis (`ai_role === 'analyst'` + `scope_label`)

Belum ada: feedback 👍/👎, admin dashboard, deploy.
