# Desain — Asisten Satu Data Penyakit (fe/)

Acuan visual: **SATUSEHAT / Kemenkes RI** (diambil dari situs live `satusehat.kemkes.go.id` + Pedoman Identitas Kemenkes 2024).

## Warna
| Token | HEX | Peran |
|-------|-----|-------|
| `--accent` | `#16B3AC` | Aksen utama teal (tombol primer, tautan, sesi aktif) |
| `--accent-dark` | `#0E8E88` | Hover / teal gelap (turquoise gelap Kemenkes) |
| `--accent-bg` | `#DFF3F1` | Latar pudar untuk badge & item aktif |
| `--accent-fg` | `#0E8E88` | Teks di atas latar pudar |
| `--canvas` | `#F6F8F8` | Latar aplikasi |
| `--surface` | `#FFFFFF` | Kartu / bubble asisten |
| `--border` | `#DFE6E5` | Garis pembatas |
| `--text` | `#232B30` | Teks utama |
| `--muted` | `#68747A` | Teks sekunder |
| `--ink` | `#232B30` | — |

Semantic: merah `#FDEBEC/#9F2F2D` (denied/error), kuning `#FBF3DB/#956400` (smalltalk).
Gradient resmi SATUSEHAT (hanya untuk media/hero bila perlu): `linear-gradient(91deg,#16B3AC,#20D6CD)`; highlight terang di latar gelap: `#28EBC8`.

## Font
- UI/body: **Manrope** (via Google Fonts, fallback `system-ui, "Segoe UI"`).
  - Kemenkes menyebut VAG Rounded BT sebagai font utama; Manrope adalah padanan web-safe yang dipakai situs SATUSEHAT sendiri.
- Kode/SQL/meta: **Roboto Mono**.

## Komponen
- Tombol primer: teal solid, radius 6–10px, teks putih, hover lebih gelap. Sekunder: putih ber-border teal.
- Bubble user: teal solid (`--accent`) teks putih. Bubble asisten: putih ber-border.
- Sesi aktif di sidebar: latar `--accent-bg`, tanpa border, teks 600.
- Tetap flat: tanpa gradient body, tanpa shadow berat (hanya bayangan samar teal di kartu login).
- Aksesibilitas: fokus pakai outline teal; gerakan mengikuti `prefers-reduced-motion`.

## Jangan
- Jangan ganti aksen ke biru/ungu; teal `#16B3AC` adalah identitas.
- Jangan pakai Inter/Roboto (bukan) untuk UI — Manrope.
- Jangan tambah emoji sebagai ikon.
