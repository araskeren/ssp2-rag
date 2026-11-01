from datetime import UTC, datetime
from types import SimpleNamespace

from ai_service.config import Settings
from ai_service.llm.crypto import SecretBox, generate_key
from ai_service.llm.registry import LlmRegistry
from ai_service.llm.repo import RegistryRows

BOX = SecretBox(generate_key())
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _provider(pid, name, kind, key="", active=True, updated=T0):
    return SimpleNamespace(
        id=pid,
        name=name,
        kind=kind,
        base_url=f"https://{name}.example",
        api_key_enc=BOX.encrypt(key) if key else None,
        headers={},
        timeout_s=None,
        is_active=active,
        updated_at=updated,
    )


def _model(mid, pid, name, kind="chat", active=True, dim=None, updated=T0):
    return SimpleNamespace(
        id=mid,
        provider_id=pid,
        name=name,
        model=f"{name}-upstream",
        kind=kind,
        temperature=None,
        max_tokens=None,
        embedding_dim=dim,
        params={},
        is_active=active,
        updated_at=updated,
    )


def _assign(purpose, model_id, ai_role=None):
    return SimpleNamespace(purpose=purpose, ai_role=ai_role, model_id=model_id)


class FakeRepo:
    def __init__(self, rows: RegistryRows) -> None:
        self.rows = rows
        self.loads = 0

    async def snapshot(self) -> RegistryRows:
        self.loads += 1
        return self.rows


def _rows(**over) -> RegistryRows:
    providers = over.get(
        "providers",
        [
            _provider(1, "oa", "openai_compatible", "sk-oa"),
            _provider(2, "anth", "anthropic_compatible", "sk-an"),
        ],
    )
    models = over.get(
        "models",
        [
            _model(10, 1, "gpt"),
            _model(11, 2, "claude"),
            _model(12, 1, "emb", kind="embedding", dim=1024),
            _model(13, 2, "claude-off", active=False),
        ],
    )
    assignments = over.get(
        "assignments",
        [_assign("chat", 10), _assign("chat", 11, "analyst"), _assign("embedding", 12)],
    )
    return RegistryRows(providers, models, assignments)


def _registry(repo, clock=None, **settings):
    built: list = []

    def factory(spec, settings_, *, temperature=None):
        built.append((spec.name, temperature))
        return SimpleNamespace(spec=spec, temperature=temperature)

    reg = LlmRegistry(
        Settings(_env_file=None, llm_model="env-model", **settings),
        repo,
        BOX,
        chat_factory=factory,
        clock=clock or (lambda: 100.0),
    )
    return reg, built


async def test_resolution_order_channel_role_default_env():
    reg, _ = _registry(FakeRepo(_rows()))
    public = await reg.resolve("chat", "public")
    assert (
        public.spec.name == "gpt" and public.via == "default" and public.label == "oa/gpt-upstream"
    )
    analyst = await reg.resolve("chat", "analyst")
    assert analyst.spec.name == "claude" and analyst.via == "role:analyst"
    assert analyst.spec.provider.kind == "anthropic_compatible"
    assert analyst.spec.provider.api_key == "sk-an"  # didekripsi
    override = await reg.resolve("chat", "analyst", model_id=10)
    assert override.spec.name == "gpt" and override.via == "channel"
    # override ke model nonaktif / embedding → jatuh ke penugasan role
    assert (await reg.resolve("chat", "analyst", model_id=13)).spec.name == "claude"
    assert (await reg.resolve("chat", "analyst", model_id=12)).spec.name == "claude"

    empty, _ = _registry(FakeRepo(RegistryRows([], [], [])))
    env = await empty.resolve("chat", "analyst")
    assert env.via == "env" and env.label == "env-model" and env.spec.source == "env"


async def test_sql_and_judge_fallbacks():
    reg, built = _registry(FakeRepo(_rows()))
    sql = await reg.resolve("sql")
    assert sql.spec.name == "gpt" and sql.via == "default"
    assert ("gpt", 0.0) in built  # SQL dipaksa temperature 0 bila model tidak menentukan
    judge = await reg.resolve("judge")
    assert judge.spec.name == "gpt" and judge.via == "default"

    reg2, _ = _registry(FakeRepo(_rows()), llm_sql_model="env-sql")
    assert (await reg2.resolve("sql")).label == "env-sql"

    reg3, _ = _registry(FakeRepo(_rows(assignments=[_assign("sql", 11), _assign("judge", 11)])))
    assert (await reg3.resolve("sql")).spec.name == "claude"
    assert (await reg3.resolve("judge")).via == "judge"
    assert (await reg3.resolve("chat")).via == "env"


async def test_fixed_models_win_and_judge_uses_fixed_chat():
    fixed = SimpleNamespace(name="fixed")
    reg = LlmRegistry(
        Settings(_env_file=None), FakeRepo(_rows()), BOX, fixed={"chat": fixed, "sql": None}
    )
    assert (await reg.resolve("chat", "analyst")).llm is fixed
    assert (await reg.resolve("judge")).llm is fixed
    assert (await reg.resolve("sql")).via == "default"


async def test_inactive_provider_and_bad_secret_are_skipped():
    providers = [
        _provider(1, "oa", "openai_compatible", "sk-oa", active=False),
        _provider(2, "anth", "anthropic_compatible"),
    ]
    providers[1].api_key_enc = SecretBox(generate_key()).encrypt("lain")  # kunci berbeda
    reg, _ = _registry(FakeRepo(_rows(providers=providers)))
    snap = await reg.snapshot()
    assert snap.models == {}
    assert (await reg.resolve("chat", "analyst")).via == "env"


async def test_ttl_invalidate_and_instance_cache():
    now = [100.0]
    repo = FakeRepo(_rows())
    reg, built = _registry(repo, clock=lambda: now[0], llm_registry_ttl_s=30)
    a = await reg.resolve("chat")
    b = await reg.resolve("chat")
    assert a.llm is b.llm and repo.loads == 1 and built.count(("gpt", None)) == 1

    now[0] += 31
    await reg.resolve("chat")
    assert (
        repo.loads == 2 and built.count(("gpt", None)) == 1
    )  # tidak berubah → instance dipakai ulang

    repo.rows = _rows(models=[_model(10, 1, "gpt", updated=datetime(2026, 11, 2, tzinfo=UTC))])
    reg.invalidate()
    c = await reg.resolve("chat")
    assert repo.loads == 3 and c.llm is not a.llm and built.count(("gpt", None)) == 2


async def test_embedding_spec_from_db_or_env():
    reg, _ = _registry(FakeRepo(_rows()))
    spec = await reg.resolve_embedding_spec()
    assert spec.source == "db" and spec.model == "emb-upstream" and spec.embedding_dim == 1024
    empty, _ = _registry(
        FakeRepo(RegistryRows([], [], [])), embedding_model="bge-m3", embedding_dim=1024
    )
    env = await empty.resolve_embedding_spec()
    assert env.source == "env" and env.model == "bge-m3" and env.embedding_dim == 1024


async def test_repo_failure_falls_back_to_env():
    class Broken:
        async def snapshot(self):
            raise RuntimeError("db down")

    reg, _ = _registry(Broken())
    assert (await reg.resolve("chat")).via == "env"
