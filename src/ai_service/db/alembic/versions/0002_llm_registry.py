"""registry provider LLM: llm_providers, llm_models, llm_assignments + override model per kanal

Revision ID: 0002_llm_registry
Revises: 0001_initial
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op

revision = "0002_llm_registry"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE llm_providers (
            id            bigserial PRIMARY KEY,
            name          text NOT NULL UNIQUE,
            kind          text NOT NULL
                          CHECK (kind IN ('openai_compatible','anthropic_compatible')),
            base_url      text NOT NULL DEFAULT '',
            api_key_enc   text,
            api_key_hint  text,
            headers       jsonb NOT NULL DEFAULT '{}',
            timeout_s     real,
            is_active     boolean NOT NULL DEFAULT true,
            notes         text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE llm_models (
            id             bigserial PRIMARY KEY,
            provider_id    bigint NOT NULL REFERENCES llm_providers(id) ON DELETE RESTRICT,
            name           text NOT NULL UNIQUE,
            model          text NOT NULL,
            kind           text NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','embedding')),
            temperature    real,
            max_tokens     int CHECK (max_tokens IS NULL OR max_tokens > 0),
            embedding_dim  int CHECK (embedding_dim IS NULL OR embedding_dim > 0),
            params         jsonb NOT NULL DEFAULT '{}',
            is_active      boolean NOT NULL DEFAULT true,
            notes          text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (kind <> 'embedding' OR embedding_dim IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX llm_models_provider_idx ON llm_models (provider_id)")
    op.execute(
        """
        CREATE TABLE llm_assignments (
            id          bigserial PRIMARY KEY,
            purpose     text NOT NULL CHECK (purpose IN ('chat','sql','judge','embedding')),
            ai_role     text CHECK (ai_role IN ('public','analyst')),
            model_id    bigint NOT NULL REFERENCES llm_models(id) ON DELETE RESTRICT,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            CHECK (ai_role IS NULL OR purpose = 'chat')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX llm_assignments_target_uq "
        "ON llm_assignments (purpose, coalesce(ai_role, '*'))"
    )
    op.execute(
        "ALTER TABLE allowed_channels ADD COLUMN llm_model_id bigint "
        "REFERENCES llm_models(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE allowed_channels DROP COLUMN IF EXISTS llm_model_id")
    for table in ("llm_assignments", "llm_models", "llm_providers"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
