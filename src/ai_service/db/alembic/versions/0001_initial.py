"""skema awal ai-service: dokumen + chunk pgvector, kanal, sesi chat, audit SQL, job ingest

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import context, op

from ai_service.config import get_settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _settings():
    return context.config.attributes.get("settings") or get_settings()


def upgrade() -> None:
    settings = _settings()
    dim = int(settings.embedding_dim)
    model = settings.embedding_model.replace("'", "''")

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE documents (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            filename      text NOT NULL,
            mime_type     text NOT NULL,
            sha256        char(64) NOT NULL UNIQUE,
            size_bytes    bigint NOT NULL,
            title         text,
            source        text,
            tags          text[] NOT NULL DEFAULT '{}',
            metadata      jsonb NOT NULL DEFAULT '{}',
            content       text NOT NULL,
            page_count    int,
            chunk_count   int NOT NULL DEFAULT 0,
            status        text NOT NULL CHECK (status IN ('pending','indexed','failed','deleted')),
            error         text,
            storage_path  text,
            uploaded_by   text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            indexed_at    timestamptz
        )
        """
    )
    op.execute("CREATE INDEX documents_status_idx ON documents (status)")
    op.execute("CREATE INDEX documents_tags_idx ON documents USING gin (tags)")

    op.execute(
        f"""
        CREATE TABLE document_chunks (
            id            uuid PRIMARY KEY,
            document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index   int NOT NULL,
            page          int,
            section       text,
            content       text NOT NULL,
            metadata      jsonb NOT NULL DEFAULT '{{}}',
            embedding     vector({dim}) NOT NULL,
            -- stem Indonesia (bobot A) + token mentah 'simple' (bobot B): stemmer Snowball
            -- Indonesia lossy (pengiriman→irim, dikirim→kirim), token mentah menjaga recall
            content_tsv   tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('pg_catalog.indonesian', content), 'A')
                || setweight(to_tsvector('pg_catalog.simple', content), 'B')
            ) STORED,
            UNIQUE (document_id, chunk_index)
        )
        """
    )
    op.execute(
        "CREATE INDEX document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX document_chunks_tsv_gin ON document_chunks USING gin (content_tsv)")
    op.execute("CREATE INDEX document_chunks_doc_idx ON document_chunks (document_id, chunk_index)")

    op.execute(
        """
        CREATE TABLE embedding_config (
            id         smallint PRIMARY KEY CHECK (id = 1),
            model      text NOT NULL,
            dim        int NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"INSERT INTO embedding_config (id, model, dim) VALUES (1, '{model}', {dim})")

    op.execute(
        """
        CREATE TABLE allowed_channels (
            id                  bigserial PRIMARY KEY,
            platform            text NOT NULL CHECK (platform IN ('wa','tg','web')),
            channel_id          text NOT NULL,
            role                text NOT NULL CHECK (role IN ('public','analyst')),
            scope_level         text NOT NULL DEFAULT 'GLOBAL'
                                CHECK (scope_level IN ('GLOBAL','PROVINCE','KABKOTA','FASKES')),
            provinsi_kode       text,
            kabupaten_kota_kode text,
            facility_id         int,
            display_name        text,
            notes               text,
            is_active           boolean NOT NULL DEFAULT true,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (platform, channel_id),
            CHECK (
                scope_level = 'GLOBAL'
                OR (scope_level = 'PROVINCE' AND provinsi_kode IS NOT NULL)
                OR (scope_level = 'KABKOTA' AND kabupaten_kota_kode IS NOT NULL)
                OR (scope_level = 'FASKES' AND facility_id IS NOT NULL)
            )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE chat_sessions (
            session_id      text PRIMARY KEY,
            platform        text NOT NULL CHECK (platform IN ('web','wa','tg')),
            principal_id    text NOT NULL,
            ai_role         text NOT NULL,
            title           text,
            message_count   int NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_message_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX chat_sessions_principal_idx ON chat_sessions (platform, principal_id, last_message_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE chat_messages (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            session_id    text NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
            role          text NOT NULL CHECK (role IN ('user','assistant')),
            content       text NOT NULL,
            intent        text,
            access_denied boolean NOT NULL DEFAULT false,
            sources       jsonb,
            sql_result    jsonb,
            model         text,
            token_usage   jsonb,
            latency_ms    int,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX chat_messages_session_idx ON chat_messages (session_id, created_at)")

    op.execute(
        """
        CREATE TABLE sql_audit_log (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            created_at    timestamptz NOT NULL DEFAULT now(),
            session_id    text,
            message_id    uuid,
            principal_id  text NOT NULL,
            platform      text NOT NULL,
            ai_role       text NOT NULL,
            scope         jsonb NOT NULL,
            backend       text NOT NULL,
            question      text NOT NULL,
            generated_sql text,
            executed_sql  text,
            views         text[] NOT NULL DEFAULT '{}',
            attempt       int NOT NULL DEFAULT 1,
            status        text NOT NULL CHECK (status IN ('ok','rejected','error','timeout','access_denied')),
            error         text,
            row_count     int,
            elapsed_ms    int
        )
        """
    )
    op.execute("CREATE INDEX sql_audit_log_created_idx ON sql_audit_log (created_at DESC)")
    op.execute(
        "CREATE INDEX sql_audit_log_principal_idx ON sql_audit_log (principal_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE ingest_jobs (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            status        text NOT NULL CHECK (status IN ('queued','running','done','failed')),
            total_files   int NOT NULL,
            done_files    int NOT NULL DEFAULT 0,
            failed_files  int NOT NULL DEFAULT 0,
            items         jsonb NOT NULL DEFAULT '[]',
            requested_by  text,
            error         text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            started_at    timestamptz,
            finished_at   timestamptz
        )
        """
    )


def downgrade() -> None:
    for table in (
        "ingest_jobs",
        "sql_audit_log",
        "chat_messages",
        "chat_sessions",
        "allowed_channels",
        "embedding_config",
        "document_chunks",
        "documents",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
