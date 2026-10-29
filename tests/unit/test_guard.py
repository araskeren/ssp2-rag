import pytest

from ai_service.sql.guard import GuardError, validate_sql

VIEWS = {"dn_sari_surveilans", "dn_ili_surveilans", "dim_regions"}


def _ok(sql, **kw):
    return validate_sql(sql, "postgres", VIEWS, max_rows=200, **kw)


def test_valid_select_gets_limit_clamped():
    v = _ok(
        "SELECT facility_provinsi_name AS provinsi, COUNT(DISTINCT record_id) AS jumlah FROM dn_sari_surveilans GROUP BY 1 ORDER BY 2 DESC"
    )
    assert v.views == ["dn_sari_surveilans"] and v.limit == 200 and "LIMIT 200" in v.sql
    big = _ok("SELECT record_id FROM dn_sari_surveilans LIMIT 5000")
    assert big.limit == 200 and "LIMIT 200" in big.sql
    small = _ok("SELECT record_id FROM dn_sari_surveilans LIMIT 10;")
    assert small.limit == 10


def test_cte_split_and_join_views():
    v = _ok(
        "WITH kasus AS (SELECT record_id, facility_provinsi_kode FROM dn_sari_surveilans) SELECT r.name, COUNT(*) AS n FROM kasus k JOIN dim_regions r ON r.code = k.facility_provinsi_kode GROUP BY r.name"
    )
    assert set(v.views) == {"dn_sari_surveilans", "dim_regions"}
    assert len(v.ctes) == 1 and v.ctes[0].startswith("kasus AS (") and v.body.startswith("SELECT")
    assert "WITH" not in v.body.upper().split("SELECT")[0]


@pytest.mark.parametrize(
    ("sql", "pesan"),
    [
        ("DELETE FROM dn_sari_surveilans", "hanya SELECT"),
        ("INSERT INTO dn_sari_surveilans VALUES (1)", "hanya SELECT"),
        ("DROP TABLE dn_sari_surveilans", "hanya SELECT"),
        ("SELECT 1; SELECT 2", "satu statement"),
        ("SELECT record_id FROM dn_sari_surveilans -- komentar", "komentar"),
        ("SELECT record_id FROM sari.records", "schema"),
        ("SELECT * FROM master.patients", "schema"),
        ("SELECT * FROM tabel_lain", "tidak dikenal"),
        ("SELECT pg_sleep(10) FROM dn_sari_surveilans", "tidak diizinkan"),
        ("SELECT current_setting('x') FROM dn_sari_surveilans", "tidak diizinkan"),
        ("SELECT record_id FROM dn_sari_surveilans WHERE record_id = $1", "tidak diizinkan"),
        ("WITH dim_regions AS (SELECT 1 AS x) SELECT x FROM dim_regions", "menyamai nama view"),
        ("SELECT * FROM generate_series(1, 10)", "tidak diizinkan"),
        ("SELECT 1", "minimal satu view"),
        ("SELECT record_id FROM dn_sari_surveilans FOR UPDATE", "tidak diizinkan"),
        ("SELECT record_id FROM dn_sari_surveilans LIMIT abc", "LIMIT"),
        ("", "kosong"),
    ],
)
def test_rejections(sql, pesan):
    with pytest.raises(GuardError, match=pesan):
        _ok(sql, max_views=3)


def test_max_views():
    with pytest.raises(GuardError, match="maksimal"):
        _ok(
            "SELECT a.record_id FROM dn_sari_surveilans a, dn_ili_surveilans b, dim_regions c",
            max_views=2,
        )


def test_union_limit_and_depth():
    v = _ok(
        "SELECT record_id FROM dn_sari_surveilans UNION ALL SELECT record_id FROM dn_ili_surveilans"
    )
    assert v.limit == 200 and set(v.views) == {"dn_sari_surveilans", "dn_ili_surveilans"}
    deep = "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT record_id FROM dn_sari_surveilans) a) b) c) d"
    with pytest.raises(GuardError, match="terlalu dalam"):
        _ok(deep)


def test_unknown_column_rejected_with_suggestion():
    cols = {
        "dn_sari_surveilans": {"record_id", "facility_provinsi_name", "epi_year_record"},
        "dim_regions": {"code", "name"},
    }
    with pytest.raises(GuardError, match="provinsi_name.*facility_provinsi_name"):
        validate_sql(
            "SELECT provinsi_name, COUNT(record_id) FROM dn_sari_surveilans GROUP BY provinsi_name",
            "postgres",
            VIEWS,
            200,
            view_columns=cols,
        )
    ok = validate_sql(
        "SELECT facility_provinsi_name AS provinsi, COUNT(record_id) AS n FROM dn_sari_surveilans GROUP BY provinsi ORDER BY n DESC",
        "postgres",
        VIEWS,
        200,
        view_columns=cols,
    )
    assert ok.views == ["dn_sari_surveilans"]
    ok2 = validate_sql(
        "SELECT s.facility_provinsi_name, r.name FROM dn_sari_surveilans s JOIN dim_regions r ON r.code = s.facility_provinsi_name",
        "postgres",
        VIEWS,
        200,
        view_columns=cols,
    )
    assert set(ok2.views) == {"dn_sari_surveilans", "dim_regions"}
    with pytest.raises(GuardError, match="kolom 'nama'"):
        validate_sql(
            "SELECT s.nama FROM dn_sari_surveilans s", "postgres", VIEWS, 200, view_columns=cols
        )
