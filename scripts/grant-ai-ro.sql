-- =============================================================
-- Role read-only `ai_ro` untuk Text-to-SQL ai-service pada DB BE satu_data_penyakit.
-- Dijalankan DBA (superuser) — idempoten, aman diulang saat ada schema/tabel baru:
--   psql -h <host> -U postgres -d satu_data_penyakit -v ai_ro_password='<rahasia>' -f scripts/grant-ai-ro.sql
-- Prinsip:
--   * SELECT hanya pada tabel yang dibutuhkan katalog view (bukan seluruh schema);
--   * master.patients hanya kolom non-PII (superset exclude-list Debezium data-pipeline);
--   * TIDAK ADA grant pada master.users/roles/permissions/user_sessions;
--   * koneksi dipaksa read-only + statement_timeout di level role.
-- =============================================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_ro') THEN
    CREATE ROLE ai_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 6;
  END IF;
END $$;

ALTER ROLE ai_ro WITH LOGIN PASSWORD :'ai_ro_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 6;
ALTER ROLE ai_ro SET default_transaction_read_only = on;
ALTER ROLE ai_ro SET statement_timeout = '15s';
ALTER ROLE ai_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE ai_ro SET search_path = pg_catalog;

GRANT CONNECT ON DATABASE :"DBNAME" TO ai_ro;

DO $$
DECLARE
  s text;
  t text;
BEGIN
  FOREACH s IN ARRAY ARRAY['master','covid_19','ili','sari','ispa','hpv','monkey_pox','pd3i','pie','karkes','epi_intel'] LOOP
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO ai_ro', s);
    END IF;
  END LOOP;

  FOREACH t IN ARRAY ARRAY[
    'master.facilities', 'master.regions',
    'covid_19.records', 'covid_19.specimens', 'covid_19.test_results',
    'ili.records', 'ili.specimens', 'ili.test_results', 'ili.aggregate_reports',
    'sari.records', 'sari.specimens', 'sari.test_results', 'sari.aggregate_reports',
    'ispa.records', 'ispa.specimens', 'ispa.test_results', 'ispa.reports', 'ispa.profiles',
    'hpv.records', 'hpv.specimens', 'hpv.test_results', 'hpv.aggregate_reports',
    'monkey_pox.records', 'monkey_pox.specimens', 'monkey_pox.test_results',
    'pd3i.records', 'pd3i.specimens', 'pd3i.test_results',
    'pie.records', 'pie.specimens', 'pie.test_results',
    'karkes.aggregate_reports', 'epi_intel.klb_events'
  ] LOOP
    IF to_regclass(t) IS NOT NULL THEN
      EXECUTE format('GRANT SELECT ON %s TO ai_ro', t);
    END IF;
  END LOOP;

  -- Pasien: column-level, tanpa nama/NIK/kontak/alamat/satusehatId.
  IF to_regclass('master.patients') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT (id, sex, "birthDate", "primaryProvinceCode", "primaryCityCode", "primarySubdistrictCode", "isActive", "createdAt") ON master.patients TO ai_ro';
  END IF;
END $$;
