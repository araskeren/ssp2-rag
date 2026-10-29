# Panduan Pelaporan Kasus SARI pada Satu Data Penyakit

Dokumen contoh (bukan juknis resmi) untuk menguji asisten helpdesk. Isinya meringkas alur
pencatatan kasus Severe Acute Respiratory Infection (SARI) di aplikasi Satu Data Penyakit:
pendaftaran kasus, pengambilan dan pengiriman spesimen, pencatatan hasil laboratorium,
verifikasi, serta pelaporan agregat mingguan.

## 1. Definisi operasional

SARI adalah infeksi saluran pernapasan akut berat dengan riwayat demam atau suhu tubuh
≥ 38 °C dan batuk, dengan onset dalam 10 hari terakhir, dan memerlukan perawatan inap.
Status kasus yang dikenal aplikasi:

- **Suspek SARI**: memenuhi definisi klinis, belum ada hasil laboratorium.
- **Probable SARI**: suspek dengan hasil laboratorium inkonklusif namun gambaran klinis kuat.
- **Konfirmasi**: hasil laboratorium positif untuk patogen target (influenza, SARS-CoV-2, RSV).

Faskes sentinel SARI adalah rumah sakit yang ditetapkan Dinas Kesehatan Provinsi.
Puskesmas tidak mencatat kasus SARI individual, tetapi dapat melakukan rujukan.

## 2. Pendaftaran kasus

1. Masuk ke menu **SARI → Kasus → Tambah Kasus**.
2. Cari pasien berdasarkan NIK. Bila belum ada, lengkapi data pasien (nama, tanggal lahir,
   jenis kelamin, alamat domisili). Data pasien disimpan di master pasien dan dipakai lintas modul.
3. Isi tanggal kunjungan, tanggal mulai gejala, suhu tubuh, dan daftar gejala.
4. Pilih faskes pelapor. Faskes pelapor menentukan wilayah pelaporan pada laporan agregat.
5. Simpan. Kasus masuk tahap **Pendaftaran** dengan status Suspek SARI.

Catatan: kasus yang tersimpan hanya dapat diubah oleh operator faskes pelapor selama
tahap Pendaftaran dan Laboratorium. Setelah diverifikasi, perubahan harus melalui admin provinsi.

## 3. Pengambilan dan pengiriman spesimen

Spesimen diambil maksimal 3 hari setelah onset gejala. Jenis spesimen yang diterima:
swab nasofaring, swab orofaring, atau aspirat trakea. Setiap spesimen memiliki nomor spesimen
unik yang dicetak pada label tabung.

Langkah pengiriman:

1. Buka kasus, pilih tab **Spesimen → Tambah Spesimen**, isi tanggal ambil dan jenis spesimen.
2. Pilih laboratorium tujuan (Labkes provinsi atau laboratorium rujukan nasional).
3. Buat **batch pengiriman** dari menu Pengiriman; satu batch dapat memuat banyak spesimen
   dengan tujuan laboratorium yang sama.
4. Cetak surat pengantar dan tempel pada boks pengiriman. Suhu pengiriman 2–8 °C.

Laboratorium mencatat tanggal penerimaan. Spesimen yang rusak atau label tidak sesuai
ditandai **REJECTED** disertai alasan penolakan, dan faskes diminta mengirim spesimen ulang.

## 4. Pencatatan hasil laboratorium

Petugas laboratorium mengisi hasil pada tab **Hasil Pemeriksaan**:

- jenis pemeriksaan (RT-PCR Influenza, RT-PCR SARS-CoV-2, PCR RSV),
- tanggal periksa dan tanggal hasil,
- kesimpulan: Positif, Negatif, atau Inkonklusif.

Bila hasil positif, status kasus otomatis menjadi **Konfirmasi** dan tahap berubah ke
**Verifikasi**. Tanggal hasil efektif yang dipakai laporan adalah tanggal hasil; bila kosong,
dipakai tanggal periksa.

## 5. Verifikasi dan penutupan kasus

Admin provinsi memverifikasi kelengkapan data (identitas pasien, spesimen, hasil). Kasus
yang lengkap ditandai **Terverifikasi** dan tahap menjadi **Selesai**. Kasus dapat ditolak
verifikasinya dengan catatan; operator faskes akan menerima notifikasi untuk memperbaiki.

Kondisi akhir pasien (sembuh, dirawat, meninggal) dan tanggal keluar wajib diisi sebelum
penutupan kasus.

## 6. Laporan agregat mingguan

Selain kasus individual, faskes sentinel mengirim **laporan agregat** per minggu epidemiologi
(ISO week, Senin–Minggu) berisi jumlah kunjungan, jumlah kasus SARI, jumlah rawat inap,
dan jumlah spesimen per kelompok umur: < 1 tahun, 1–4, 5–9, 10–14, 15–59, dan ≥ 60 tahun.

Batas pengiriman laporan agregat adalah hari Selasa minggu berikutnya. Laporan yang terlambat
tetap bisa dikirim tetapi ditandai terlambat pada dashboard kelengkapan.

## 7. Hak akses

| Peran | Kemampuan |
| --- | --- |
| Operator Faskes | mencatat kasus, spesimen, dan laporan agregat faskesnya |
| Admin KabKota | melihat seluruh kasus di kabupaten/kota, verifikasi awal |
| Admin Provinsi | verifikasi akhir, mengelola faskes sentinel |
| Eksekutif / Viewer | melihat dashboard dan laporan sesuai wilayah |

Pertanyaan teknis dapat disampaikan melalui helpdesk Pusdatin pada jam kerja.
