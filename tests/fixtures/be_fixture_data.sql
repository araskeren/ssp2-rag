-- Data fixture deterministik (generate_series) untuk tes Text-to-SQL. Jangan dipakai di produksi.
INSERT INTO master.regions (id, code, name, level, "parentId") VALUES
  (1, '31', 'DKI Jakarta', 'PROVINCE', NULL),
  (2, '33', 'Jawa Tengah', 'PROVINCE', NULL),
  (3, '31.71', 'Kota Jakarta Pusat', 'CITY', 1),
  (4, '33.28', 'Kabupaten Pati', 'CITY', 2),
  (5, '33.74', 'Kota Semarang', 'CITY', 2);
SELECT setval('master.regions_id_seq', 5);

INSERT INTO master.facilities (id, name, "jenisSarana", "subJenis", "provinsiKode", "kabupatenKotaKode", "statusSarana") VALUES
  (1, 'Kementerian Kesehatan RI', 'Kementerian & Lembaga', NULL, '31', '31.71', 'Aktif'),
  (2, 'Dinas Kesehatan Provinsi Jawa Tengah', 'Dinas Kesehatan', 'Dinas Kesehatan Provinsi', '33', '33.74', 'Aktif'),
  (3, 'Puskesmas Pati I', 'Pusat Kesehatan Masyarakat', 'Puskesmas', '33', '33.28', 'Aktif'),
  (4, 'Dinas Kesehatan Kota Semarang', 'Dinas Kesehatan', 'Dinas Kesehatan Kota', '33', '33.74', 'Aktif'),
  (5, 'RSUP Dr. Kariadi', 'Rumah Sakit', 'RS Umum', '33', '33.74', 'Aktif'),
  (6, 'RSUD Jakarta Pusat', 'Rumah Sakit', 'RS Umum', '31', '31.71', 'Aktif'),
  (7, 'Labkesda Jawa Tengah', 'Laboratorium Kesehatan', 'Labkesda', '33', '33.74', 'Aktif');
SELECT setval('master.facilities_id_seq', 7);

INSERT INTO master.users (name, email, password, "roleId", "facilityId") VALUES
  ('Admin Kemenkes', 'admin@kemkes.go.id', 'hash-rahasia', 4, 1);

INSERT INTO master.patients (nik, name, sex, "birthDate", "primaryProvinceCode", "primaryCityCode", "primaryStreet", "primaryContactNo")
SELECT
  '33' || lpad(g::text, 14, '0'),
  'Pasien ' || g,
  CASE WHEN g % 2 = 0 THEN 'MALE' ELSE 'FEMALE' END,
  DATE '1950-01-01' + (g * 730),
  CASE WHEN g % 3 = 0 THEN '31' ELSE '33' END,
  CASE WHEN g % 3 = 0 THEN '31.71' WHEN g % 3 = 1 THEN '33.28' ELSE '33.74' END,
  'Jl. Rahasia No. ' || g,
  '08' || lpad(g::text, 10, '0')
FROM generate_series(1, 30) AS g;

-- SARI: 60 kasus Jan 2025 → Des 2025 (tiap 6 hari), faskes 3/5/6, status bergilir.
INSERT INTO sari.records (id, "patientId", status, tanggal_kunjungan, "currentStage", "facilityId", suhu_tubuh, tanggal_mulai_gejala, kondisi_akhir, tanggal_keluar, "createdAt", "updatedAt")
SELECT
  uuidv7(),
  (SELECT id FROM master.patients ORDER BY nik LIMIT 1 OFFSET ((g - 1) % 30)),
  CASE g % 3 WHEN 0 THEN 'Suspek SARI' WHEN 1 THEN 'Probable SARI' ELSE 'Konfirmasi SARI' END,
  DATE '2025-01-06' + (g * 6),
  CASE WHEN g % 5 = 0 THEN 'Laboratorium' ELSE 'Selesai' END,
  CASE g % 3 WHEN 0 THEN 3 WHEN 1 THEN 5 ELSE 6 END,
  37.5 + (g % 4) * 0.4,
  DATE '2025-01-06' + (g * 6) - 2,
  CASE g % 4 WHEN 0 THEN 'Sembuh / Dipulangkan' WHEN 1 THEN 'Dirujuk' WHEN 2 THEN 'Meninggal Dunia' ELSE NULL END,
  CASE WHEN g % 4 = 3 THEN NULL ELSE DATE '2025-01-06' + (g * 6) + 5 END,
  (DATE '2025-01-06' + (g * 6))::timestamp + TIME '08:30',
  (DATE '2025-01-06' + (g * 6))::timestamp + TIME '08:30'
FROM generate_series(1, 60) AS g;

-- Spesimen untuk 45 kasus (g % 4 <> 0); tiap ke-7 ditolak.
INSERT INTO sari.specimens (id, "recordId", tanggal_ambil, nomor_spesimen, tujuan_pemeriksaan, jenis_spesimen, tanggal_pengiriman, tanggal_penerimaan, lab_tujuan_id, status, alasan_penolakan, "facilityId", "createdAt", "updatedAt")
SELECT
  uuidv7(),
  r.id,
  r.tanggal_kunjungan + 1,
  'SARI-' || rn,
  'Diagnostik',
  CASE WHEN rn % 2 = 0 THEN 'Nasofaring' ELSE 'Sputum' END,
  r.tanggal_kunjungan + 2,
  r.tanggal_kunjungan + 3,
  7,
  CASE WHEN rn % 7 = 0 THEN 'REJECTED' ELSE 'RECEIVED' END,
  CASE WHEN rn % 7 = 0 THEN 'Label tidak sesuai' ELSE NULL END,
  r."facilityId",
  r."createdAt",
  r."createdAt"
FROM (SELECT id, tanggal_kunjungan, "facilityId", "createdAt", row_number() OVER (ORDER BY "createdAt") AS rn FROM sari.records) r
WHERE rn % 4 <> 0;

-- Hasil lab untuk spesimen RECEIVED; tanggal_hasil kosong tiap ke-5.
INSERT INTO sari.test_results (id, "specimenId", tanggal_periksa, tanggal_hasil, "jenisPemeriksaan", kesimpulan, "facilityId", "createdAt", "updatedAt")
SELECT
  uuidv7(),
  s.id,
  s.tanggal_penerimaan + 1,
  CASE WHEN rn % 5 = 0 THEN NULL ELSE s.tanggal_penerimaan + 2 END,
  'RT-PCR',
  CASE rn % 3 WHEN 0 THEN 'POSITIF_COVID' WHEN 1 THEN 'POSITIF_AGEN_LAIN' ELSE 'TIDAK_TERIDENTIFIKASI' END,
  7,
  s."createdAt",
  s."createdAt"
FROM (SELECT id, tanggal_penerimaan, "createdAt", row_number() OVER (ORDER BY "createdAt") AS rn FROM sari.specimens WHERE status = 'RECEIVED') s;

-- ILI: 40 kasus Feb 2025 → Nov 2025 (tiap 7 hari).
INSERT INTO ili.records (id, "patientId", status, tanggal_kunjungan, "currentStage", "facilityId", suhu_tubuh, tanggal_mulai_gejala, "createdAt", "updatedAt")
SELECT
  uuidv7(),
  (SELECT id FROM master.patients ORDER BY nik LIMIT 1 OFFSET ((g - 1) % 30)),
  CASE WHEN g % 2 = 0 THEN 'Suspek ILI' ELSE 'Konfirmasi ILI' END,
  DATE '2025-02-03' + (g * 7),
  'Selesai',
  CASE g % 3 WHEN 0 THEN 3 WHEN 1 THEN 5 ELSE 6 END,
  38.0 + (g % 3) * 0.3,
  DATE '2025-02-03' + (g * 7) - 1,
  (DATE '2025-02-03' + (g * 7))::timestamp + TIME '09:00',
  (DATE '2025-02-03' + (g * 7))::timestamp + TIME '09:00'
FROM generate_series(1, 40) AS g;

-- COVID-19: 30 kasus Mar 2025 → Okt 2025 (tiap 8 hari).
INSERT INTO covid_19.records (id, "patientId", status, "currentStage", "facilityId", suhu_tubuh, outcome, tanggal_outcome, "createdAt", "updatedAt")
SELECT
  uuidv7(),
  (SELECT id FROM master.patients ORDER BY nik LIMIT 1 OFFSET ((g - 1) % 30)),
  CASE g % 3 WHEN 0 THEN 'Suspek' WHEN 1 THEN 'Probable' ELSE 'Konfirmasi' END,
  'Selesai',
  CASE g % 3 WHEN 0 THEN 3 WHEN 1 THEN 5 ELSE 6 END,
  37.0 + (g % 5) * 0.3,
  CASE g % 5 WHEN 0 THEN 'Meninggal' WHEN 1 THEN NULL ELSE 'Sembuh' END,
  CASE WHEN g % 5 = 1 THEN NULL ELSE DATE '2025-03-03' + (g * 8) + 10 END,
  (DATE '2025-03-03' + (g * 8))::timestamp + TIME '10:00',
  (DATE '2025-03-03' + (g * 8))::timestamp + TIME '10:00'
FROM generate_series(1, 30) AS g;
