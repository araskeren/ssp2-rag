"""sakelar fitur asisten (feature_flags), dikelola lewat menu Manajemen › AI

Revision ID: 0003_feature_flags
Revises: 0002_llm_registry
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op

revision = "0003_feature_flags"
down_revision = "0002_llm_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feature_flags (
            key         text PRIMARY KEY
                        CHECK (key IN ('assistant','helpdesk','analysis','connector')),
            enabled     boolean NOT NULL DEFAULT true,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  text
        )
        """
    )
    op.execute(
        """
        INSERT INTO feature_flags (key) VALUES
            ('assistant'), ('helpdesk'), ('analysis'), ('connector')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE feature_flags")
