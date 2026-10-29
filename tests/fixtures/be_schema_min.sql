-- Subset skema DB BE satu_data_penyakit (branch dev/change/PIE) untuk tes & dev lokal ai-service.
-- Nama tabel/kolom (camelCase + snake_case) dan tipe id (uuid v7 / int) mengikuti entitas TypeORM.
DROP SCHEMA IF EXISTS master CASCADE;
DROP SCHEMA IF EXISTS sari CASCADE;
DROP SCHEMA IF EXISTS ili CASCADE;
DROP SCHEMA IF EXISTS covid_19 CASCADE;
CREATE SCHEMA master;
CREATE SCHEMA sari;
CREATE SCHEMA ili;
CREATE SCHEMA covid_19;

CREATE TABLE master.regions (
  id serial PRIMARY KEY,
  code varchar(20) NOT NULL,
  name varchar(255) NOT NULL,
  level varchar(20) NOT NULL,
  "parentId" int,
  "isActive" boolean NOT NULL DEFAULT true,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE master.facilities (
  id serial PRIMARY KEY,
  "kodeSatSet" varchar(50),
  "kodeSarana" varchar(50),
  name varchar(255) NOT NULL,
  "jenisSarana" varchar(100),
  "subJenis" varchar(100),
  "provinsiKode" varchar(10),
  "kabupatenKotaKode" varchar(10),
  "kecamatanKode" varchar(10),
  "kelurahanKode" varchar(15),
  "regionId" int,
  tag varchar(50),
  "statusSarana" varchar(50),
  "kategoriFasyankes" varchar(50),
  "isActive" boolean NOT NULL DEFAULT true,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE master.patients (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  nik varchar(20),
  name varchar(255),
  sex varchar(10),
  "birthDate" date,
  "primaryProvinceCode" varchar(10),
  "primaryCityCode" varchar(10),
  "primarySubdistrictCode" varchar(10),
  "primaryStreet" text,
  "primaryContactNo" varchar(30),
  "isActive" boolean NOT NULL DEFAULT true,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE master.users (
  id serial PRIMARY KEY,
  name varchar(255),
  email varchar(255),
  password varchar(255),
  "roleId" int,
  "facilityId" int
);

CREATE TABLE sari.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL,
  tanggal_kunjungan date,
  id_label varchar(100),
  "currentStage" varchar(50) NOT NULL,
  "isActive" boolean NOT NULL DEFAULT true,
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  suhu_tubuh numeric(4,2),
  frekuensi_napas int,
  gejala jsonb,
  tanggal_mulai_gejala date,
  riwayat_perjalanan jsonb,
  komorbid jsonb,
  paparan_unggas jsonb,
  tanggal_keluar date,
  kondisi_akhir varchar(50),
  diagnosa_akhir varchar(150),
  catatan text,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE sari.specimens (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "recordId" uuid NOT NULL REFERENCES sari.records(id),
  tanggal_ambil date NOT NULL,
  nomor_spesimen varchar(100),
  tujuan_pemeriksaan varchar(50),
  jenis_sampel varchar(50),
  jenis_spesimen varchar(100) NOT NULL,
  tanggal_pengiriman date,
  no_batch varchar(100),
  tujuan_pengiriman varchar(255),
  suhu_pengiriman numeric(5,2),
  tanggal_penerimaan date,
  suhu_diterima numeric(5,2),
  lab_tujuan_id int,
  status varchar(50) NOT NULL DEFAULT 'PENDING',
  alasan_penolakan text,
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE sari.test_results (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "specimenId" uuid NOT NULL REFERENCES sari.specimens(id),
  tanggal_periksa date NOT NULL,
  tanggal_hasil date,
  "jenisPemeriksaan" varchar(50) NOT NULL,
  ct_values jsonb,
  kesimpulan varchar(50) NOT NULL,
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);

CREATE TABLE ili.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL,
  tanggal_kunjungan date,
  id_label varchar(100),
  "currentStage" varchar(50) NOT NULL,
  "isActive" boolean NOT NULL DEFAULT true,
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  suhu_tubuh numeric(4,2),
  frekuensi_napas int,
  gejala jsonb,
  tanggal_mulai_gejala date,
  riwayat_perjalanan jsonb,
  komorbid jsonb,
  paparan_unggas jsonb,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE ili.specimens (LIKE sari.specimens INCLUDING ALL);
ALTER TABLE ili.specimens ADD FOREIGN KEY ("recordId") REFERENCES ili.records(id);
CREATE TABLE ili.test_results (LIKE sari.test_results INCLUDING ALL);
ALTER TABLE ili.test_results ADD FOREIGN KEY ("specimenId") REFERENCES ili.specimens(id);

CREATE TABLE covid_19.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL,
  "currentStage" varchar(50) NOT NULL,
  "isActive" boolean NOT NULL DEFAULT true,
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  suhu_tubuh numeric(4,2),
  gejala jsonb,
  riwayat_perjalanan jsonb,
  komorbid jsonb,
  inocovid_id varchar(100),
  outcome varchar(50),
  tanggal_outcome date,
  keterangan_pe text,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE covid_19.specimens (LIKE sari.specimens INCLUDING ALL);
ALTER TABLE covid_19.specimens ADD FOREIGN KEY ("recordId") REFERENCES covid_19.records(id);
CREATE TABLE covid_19.test_results (LIKE sari.test_results INCLUDING ALL);
ALTER TABLE covid_19.test_results ADD FOREIGN KEY ("specimenId") REFERENCES covid_19.specimens(id);

-- ---------------------------------------------------------------------------
-- Modul tambahan (DDL saja, tanpa data) agar seluruh view katalog bisa diuji LIMIT 0.
-- ---------------------------------------------------------------------------
DROP SCHEMA IF EXISTS ispa CASCADE;
DROP SCHEMA IF EXISTS monkey_pox CASCADE;
DROP SCHEMA IF EXISTS pd3i CASCADE;
DROP SCHEMA IF EXISTS pie CASCADE;
DROP SCHEMA IF EXISTS hpv CASCADE;
CREATE SCHEMA ispa;
CREATE SCHEMA monkey_pox;
CREATE SCHEMA pd3i;
CREATE SCHEMA pie;
CREATE SCHEMA hpv;

CREATE TABLE sari.aggregate_reports (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "facilityId" int NOT NULL,
  "epiWeek" smallint NOT NULL,
  "epiYear" smallint NOT NULL,
  "totalSpecimenCases" int NOT NULL DEFAULT 0,
  "totalSariCases" int NOT NULL DEFAULT 0,
  "totalInpatient" int NOT NULL DEFAULT 0,
  status varchar(20) NOT NULL DEFAULT 'DRAFT',
  "verifiedBy" int, "verifiedAt" timestamp, "rejectionMessage" text, "rejectedBy" int, "rejectedAt" timestamp,
  "createdBy" int, "updatedBy" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE ili.aggregate_reports (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "facilityId" int NOT NULL,
  "epiWeek" smallint NOT NULL,
  "epiYear" smallint NOT NULL,
  "totalSpecimenCases" int NOT NULL DEFAULT 0,
  "totalIliCases" int NOT NULL DEFAULT 0,
  "totalVisits" int NOT NULL DEFAULT 0,
  status varchar(20) NOT NULL DEFAULT 'DRAFT',
  "verifiedBy" int, "verifiedAt" timestamp, "rejectionMessage" text, "rejectedBy" int, "rejectedAt" timestamp,
  "createdBy" int, "updatedBy" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
INSERT INTO sari.aggregate_reports ("facilityId", "epiWeek", "epiYear", "totalSpecimenCases", "totalSariCases", "totalInpatient", status)
SELECT CASE g % 2 WHEN 0 THEN 5 ELSE 6 END, g, 2025, g, g * 2, g * 3, 'VERIFIED' FROM generate_series(1, 20) AS g;

CREATE TABLE ispa.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL, klasifikasi varchar(50), tanggal_kunjungan date, id_label varchar(100),
  "currentStage" varchar(50) NOT NULL, "isActive" boolean NOT NULL DEFAULT true, "facilityId" int, "createdById" int, "updatedById" int,
  suhu_tubuh numeric(4,2), frekuensi_napas int, gejala jsonb, tanggal_mulai_gejala date, komorbid jsonb,
  tanggal_keluar date, kondisi_akhir varchar(50), diagnosa_akhir varchar(150), catatan text,
  confirm_speciment_id uuid, confirm_at timestamp, confirm_by int, recover_speciment_id uuid, recover_at timestamp, recover_by int,
  death_speciment_id uuid, death_at timestamp, death_by int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE ispa.specimens (LIKE sari.specimens INCLUDING ALL);
ALTER TABLE ispa.specimens ADD FOREIGN KEY ("recordId") REFERENCES ispa.records(id);
CREATE TABLE ispa.test_results (LIKE sari.test_results INCLUDING ALL);
ALTER TABLE ispa.test_results ADD FOREIGN KEY ("specimenId") REFERENCES ispa.specimens(id);

CREATE TABLE monkey_pox.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL, "currentStage" varchar(50) NOT NULL, "isActive" boolean NOT NULL DEFAULT true,
  "facilityId" int, "createdById" int, "updatedById" int,
  suhu_tubuh numeric(4,2), gejala jsonb, risiko_paparan jsonb, komorbid jsonb, status_kontak varchar(50),
  outcome varchar(50), tanggal_selesai_pemantauan date, tanggal_outcome date, keterangan_pe text,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE monkey_pox.specimens (LIKE sari.specimens INCLUDING ALL);
ALTER TABLE monkey_pox.specimens ADD COLUMN foto_klinis_url varchar(255);
ALTER TABLE monkey_pox.specimens ADD FOREIGN KEY ("recordId") REFERENCES monkey_pox.records(id);
CREATE TABLE monkey_pox.test_results (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "specimenId" uuid NOT NULL REFERENCES monkey_pox.specimens(id),
  tanggal_terima date, tanggal_periksa date NOT NULL, "jenisPemeriksaan" varchar(50) NOT NULL, ct_values jsonb, kesimpulan varchar(50) NOT NULL,
  "facilityId" int, "createdById" int, "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);

CREATE TABLE pd3i.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  "syndromeType" varchar(50) NOT NULL, status varchar(50) NOT NULL, "verificationStatus" varchar(50) NOT NULL DEFAULT 'PENDING',
  "currentStage" varchar(50) NOT NULL, "isActive" boolean NOT NULL DEFAULT true, "facilityId" int, "createdById" int, "updatedById" int,
  "onsetOfSymptomsDate" date, "faskesReportDate" date, "clusterExposureData" text, "clinicalManifestation" text, "reportingFaskes" varchar(255),
  "noEpid" varchar(100), penyakit varchar(100), gejala jsonb, "statusKlb" boolean, "klbKe" integer, komplikasi jsonb, hamil boolean, "umurKehamilan" integer,
  "statusImunisasi" boolean, "riwayatImunisasi" jsonb, "sumberInformasiImunisasi" varchar(100), "tglMunculRuam" date, "tglDemam" date, "riwayatPerjalanan" jsonb,
  "sumberLaporan" varchar(100), "tanggalTerimaLaporan" date, "pemberianVitaminA" boolean, "dirawatDiRs" boolean, "rsId" integer, "kondisiSaatIni" varchar(50),
  decision varchar(50), "verificationDate" date, "verifiedBy" varchar(255), remarks text, "patientStatus" varchar(50), "statusDate" date, "mortalityEvent" boolean, notes text,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE pd3i.specimens (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "recordId" uuid NOT NULL REFERENCES pd3i.records(id),
  "specimenType" varchar(100), "sampleType" varchar(100), "examinationPurpose" text, "dateCollected" date, "labRujukan" varchar(255),
  nomor_spesimen varchar(100), tanggal_pengiriman date, no_batch varchar(100), tujuan_pengiriman varchar(255), suhu_pengiriman numeric(5,2),
  tanggal_penerimaan date, suhu_diterima numeric(5,2), lab_tujuan_id int, status varchar(50) NOT NULL DEFAULT 'PENDING', alasan_penolakan text,
  "facilityId" int, "createdById" int, "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE pd3i.test_results (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "specimenId" uuid NOT NULL REFERENCES pd3i.specimens(id),
  "jenisPemeriksaan" varchar(100), "testDate" date, remarks text, etiology varchar(100), result varchar(100),
  "facilityId" int, "createdById" int, "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);

CREATE TABLE pie.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  "syndromeType" varchar(50) NOT NULL, status varchar(50) NOT NULL, "verificationStatus" varchar(50) NOT NULL DEFAULT 'PENDING',
  "currentStage" varchar(50) NOT NULL, "isActive" boolean NOT NULL DEFAULT true, "facilityId" int, "createdById" int, "updatedById" int,
  "onsetOfSymptomsDate" date, "hospitalAdmissionDate" date, "faskesReportDate" date, "clusterExposureData" text, "clinicalManifestation" text,
  "reportingFaskes" varchar(255), "bodyTemperature" numeric(4,1), "travelledAbroad" boolean, "visitedCountries" text, "criteriaSindrom" text,
  "riskFactors" text, diagnosis varchar(255), decision varchar(50), "verificationDate" date, "verifiedBy" varchar(255), remarks text,
  "patientStatus" varchar(50), "statusDate" date, "mortalityEvent" boolean, notes text,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE pie.specimens (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "recordId" uuid NOT NULL REFERENCES pie.records(id),
  "specimenType" varchar(100), tanggal_ambil date, nomor_spesimen varchar(100), tujuan_pemeriksaan varchar(50), jenis_sampel varchar(50),
  jenis_spesimen varchar(100), "dateCollected" date, "labRujukan" varchar(255), tanggal_pengiriman date, no_batch varchar(100), tujuan_pengiriman varchar(255),
  suhu_pengiriman numeric(5,2), tanggal_penerimaan date, suhu_diterima numeric(5,2), lab_tujuan_id int, status varchar(50) NOT NULL DEFAULT 'PENDING',
  alasan_penolakan text, "facilityId" int, "createdById" int, "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE pie.test_results (LIKE pd3i.test_results INCLUDING ALL);
ALTER TABLE pie.test_results ADD FOREIGN KEY ("specimenId") REFERENCES pie.specimens(id);

CREATE TABLE hpv.records (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "patientId" uuid NOT NULL REFERENCES master.patients(id),
  status varchar(50) NOT NULL, "tanggalVerifikasi" timestamp, "verifiedById" int, "catatanVerifikasi" text,
  "currentStage" varchar(50) NOT NULL, "isActive" boolean NOT NULL DEFAULT true, "facilityId" int,
  "tanggalSkrining" date, "metodeDeteksiDini" varchar(50), "hasilIva" varchar(50), "tanggalPemeriksaanIva" date, "tindakLanjutIva" varchar(100),
  "tanggalTindakLanjutIva" date, "catatanTindakLanjutIva" text, "rujukanFacilityId" int,
  "usiaSaatPertamaBerhubunganIntim" int, "usiaSaatKehamilanPertama" int, "usiaSaatMelahirkanPertama" int, "jumlahKelahiranNormal" int,
  "jumlahKelahiranSesar" int, "jumlahKeguguran" int, "riwayatKontrasepsi" varchar(100), "riwayatKontrasepsiLainnya" varchar(100),
  "kontrasepsiSaatIni" varchar(100), "kontrasepsiSaatIniLainnya" varchar(100), "statusHiv" varchar(50), "riwayatIms" boolean,
  "riwayatKankerKeluarga" varchar(50), "jenisKankerKeluarga" text, "hubunganKankerPayudara" varchar(100), "hubunganKankerPayudaraLainnya" varchar(100),
  "riwayatImunisasiHpv" boolean, "tanggalImunisasiHpv" date, "jenisImunisasiHpv" varchar(100), "dosisImunisasiHpv" varchar(50),
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE hpv.specimens (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "recordId" uuid NOT NULL REFERENCES hpv.records(id),
  "metodePengambilan" varchar(100), "jenisSpesimen" varchar(100), "nomorSampel" varchar(100), "nomorSpesimen" varchar(100),
  "tanggalPenarikanSpesimen" date, "laboratoriumTujuanId" int, "laboratoriumTujuanRef" varchar(255), "noBatch" varchar(100),
  "tanggalPengiriman" date, "tujuanPengiriman" varchar(255), "suhuPengiriman" numeric(5,2), "tanggalPenerimaan" date, "suhuDiterima" numeric(5,2),
  status varchar(50) NOT NULL DEFAULT 'PENDING', "catatanPenerimaan" text, "kondisiSpesimen" varchar(100), "facilityId" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);
CREATE TABLE hpv.test_results (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "specimenId" uuid NOT NULL REFERENCES hpv.specimens(id),
  "hasilTes" varchar(100), "jenisPemeriksaan" varchar(100), "tanggalPemeriksaan" date, "tanggalHasil" date,
  "subtipeLain" boolean, "hasilTidakTerbaca" boolean, "facilityId" int, "createdById" int, "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(), "updatedAt" timestamp NOT NULL DEFAULT now()
);

-- Epi-Intel: KLB keracunan pangan (SKDR) — dipakai view dn_klb_events.
DROP SCHEMA IF EXISTS epi_intel CASCADE;
CREATE SCHEMA epi_intel;
CREATE TABLE epi_intel.klb_events (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  "sumberData" varchar(10) NOT NULL DEFAULT 'SKDR',
  "nomorEpid" varchar(100) NOT NULL UNIQUE,
  "mingguEpidemiologi" smallint NOT NULL,
  "tanggalKejadian" date NOT NULL,
  "tanggalLapor" date NOT NULL,
  provinsi varchar(150) NOT NULL,
  "kabupatenKota" varchar(150) NOT NULL,
  kecamatan varchar(150) NOT NULL,
  "desaKelurahan" varchar(150) NOT NULL,
  "namaPuskesmas" varchar(150) NOT NULL,
  "tempatKejadian" varchar(255) NOT NULL,
  "jenisPanganDicurigai" varchar(255) NOT NULL,
  "jumlahTerpapar" int NOT NULL DEFAULT 0,
  "jumlahSakit" int NOT NULL DEFAULT 0,
  "jumlahDirawat" int NOT NULL DEFAULT 0,
  "jumlahMeninggal" int NOT NULL DEFAULT 0,
  status varchar(15) NOT NULL DEFAULT 'BARU',
  "facilityId" int,
  "createdById" int,
  "updatedById" int,
  "createdAt" timestamp NOT NULL DEFAULT now(),
  "updatedAt" timestamp NOT NULL DEFAULT now()
);
INSERT INTO epi_intel.klb_events ("nomorEpid", "mingguEpidemiologi", "tanggalKejadian", "tanggalLapor", provinsi, "kabupatenKota", kecamatan, "desaKelurahan", "namaPuskesmas", "tempatKejadian", "jenisPanganDicurigai", "jumlahTerpapar", "jumlahSakit", "jumlahDirawat", "jumlahMeninggal", status, "facilityId")
SELECT 'KLB-2025-' || g, g, DATE '2025-01-06' + (g * 14), DATE '2025-01-06' + (g * 14) + 1,
  CASE WHEN g % 3 = 0 THEN 'DKI Jakarta' ELSE 'Jawa Tengah' END,
  CASE WHEN g % 3 = 0 THEN 'Kota Jakarta Pusat' WHEN g % 3 = 1 THEN 'Kabupaten Pati' ELSE 'Kota Semarang' END,
  'Kecamatan ' || g, 'Desa ' || g, 'Puskesmas ' || g, 'Hajatan ' || g, 'Nasi kotak', 50 + g, 20 + g, 5,
  CASE WHEN g % 4 = 0 THEN 1 ELSE 0 END,
  CASE g % 3 WHEN 0 THEN 'BARU' WHEN 1 THEN 'INVESTIGASI' ELSE 'SELESAI' END,
  CASE WHEN g % 3 = 0 THEN 6 WHEN g % 3 = 1 THEN 3 ELSE 5 END
FROM generate_series(1, 12) AS g;
