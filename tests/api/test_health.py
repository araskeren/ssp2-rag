import httpx

from ai_service.config import Settings
from ai_service.main import create_app


async def test_healthz_and_docs_toggle():
    app = create_app(Settings(_env_file=None, app_env="local"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
        assert r.status_code == 200 and r.text == "ok"
        assert "x-request-id" in r.headers
        docs = await client.get("/docs")
        assert docs.status_code == 200


async def test_docs_disabled_in_prod():
    settings = Settings(
        _env_file=None,
        app_env="prod",
        jwt_secret="s" * 32,
        api_keys="k",
        admin_api_keys="a",
        llm_base_url="http://llm/v1",
        be_base_url="http://be",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/docs")).status_code == 404


async def test_readyz_without_db_reports_down():
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["db"] == "down"


async def test_error_envelope_for_404_and_validation():
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/tidak-ada")
        assert r.status_code == 404
        body = r.json()
        assert body["statusCode"] == 404 and body["error"] == "Not Found" and "message" in body


async def test_admin_catalog_endpoint():
    from ai_service.services import Services

    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.services = Services(settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/api/v1/admin/catalog",
            headers={"X-API-Key": "dev-admin-key"},
            params={"prompt": "true"},
        )
        assert r.status_code == 200
        body = r.json()
        assert (
            body["issues"] == []
            and len(body["views"]) >= 13
            and "### dn_sari_surveilans" in body["prompt"]
        )
        assert (await client.get("/api/v1/admin/catalog")).status_code == 401
