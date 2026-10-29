import sqlglot

from ai_service.sql.catalog import load_catalog


def test_catalog_loads_and_validates_postgres():
    catalog = load_catalog()
    assert {
        "dn_sari_surveilans",
        "dn_ili_surveilans",
        "dn_covid_19_surveilans",
        "dim_facilities",
        "dim_regions",
    } <= catalog.names
    assert catalog.validate("postgres") == []
    sari = catalog.get("dn_sari_surveilans")
    assert (
        sari is not None
        and "kondisi_akhir" in sari.column_names
        and sari.scope_mapping("postgres")["facility_id"] == 'r."facilityId"'
    )
    assert catalog.get("dim_regions").unscoped is True


def test_catalog_clickhouse_sql_parses():
    catalog = load_catalog()
    for view in catalog.views.values():
        sqlglot.parse_one(
            view.sql_for("clickhouse").replace("{scope_filter}", "1"), read="clickhouse"
        )
    assert catalog.validate("clickhouse") == []


def test_prompt_rendering_mentions_views_and_columns():
    prompt = load_catalog().render_prompt("postgres", max_examples=1)
    assert (
        "### dn_sari_surveilans" in prompt and "- record_id (id)" in prompt and "Contoh:" in prompt
    )
    assert "Referensi (tidak dibatasi wilayah)" in prompt


def test_select_views_by_question():
    catalog = load_catalog()
    names = {v.name for v in catalog.select_views("Berapa kasus SARI per provinsi tahun 2025?")}
    assert "dn_sari_surveilans" in names and "dn_sari_aggregate_reports" in names
    assert "dn_ili_surveilans" not in names and "dim_regions" in names
    assert len(catalog.select_views("Berapa jumlah data?")) == len(catalog.views)
    assert {v.name for v in catalog.select_views("tren campak mingguan")} >= {"dn_pd3i_surveilans"}
    assert "dn_klb_events" in {v.name for v in catalog.select_views("KLB keracunan pangan 2025")}
