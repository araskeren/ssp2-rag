import pytest

from ai_service.sql.drift import check_drift

pytestmark = pytest.mark.pg


async def test_all_catalog_views_execute_on_fixture(analytics_dsn):
    results = await check_drift(analytics_dsn)
    problems = [(name, err) for name, err in results if err]
    assert len(results) >= 13
    assert problems == []
