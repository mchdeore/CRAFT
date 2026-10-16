"""Create library and file records with declared application grants."""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE provider_items (
        item TEXT NOT NULL,
        path TEXT NOT NULL,
        title TEXT NOT NULL,
        mission_key TEXT,
        silo TEXT,
        stage TEXT,
        simulated BOOLEAN NOT NULL,
        classification TEXT NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_provider_items PRIMARY KEY (item),
        CONSTRAINT ck_provider_items_item CHECK (item ~ '^itm_[0-9a-f]{12}$'),
        CONSTRAINT ck_provider_items_classification CHECK (classification in
        ('unclassified','protected-a','protected-b'))
        )
    """)
    op.execute("""
        GRANT SELECT ON provider_items TO craft_app
    """)
    op.execute("""
        CREATE TABLE doc_versions (
        id UUID DEFAULT uuidv7() NOT NULL,
        item TEXT NOT NULL,
        version INTEGER NOT NULL,
        sha256 BYTEA NOT NULL,
        media_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        summary_kind TEXT NOT NULL,
        summary_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
        indexed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_doc_versions PRIMARY KEY (id),
        CONSTRAINT uq_doc_versions_item_version UNIQUE (item, version),
        CONSTRAINT ck_doc_versions_sha256 CHECK (octet_length(sha256) = 32),
        CONSTRAINT ck_doc_versions_summary_kind CHECK (summary_kind in
        ('extract','generated')),
        CONSTRAINT fk_doc_versions_item_provider_items FOREIGN KEY(item) REFERENCES
        provider_items (item)
        )
    """)
    op.execute("""
        CREATE INDEX ix_doc_versions_summary_tsv ON doc_versions USING gin (summary_tsv)
    """)
    op.execute("""
        GRANT SELECT ON doc_versions TO craft_app
    """)
    op.execute("""
        CREATE TABLE chunks (
        id UUID DEFAULT uuidv7() NOT NULL,
        doc_version_id UUID NOT NULL,
        block INTEGER NOT NULL,
        kind TEXT NOT NULL,
        heading_path TEXT NOT NULL,
        locator TEXT NOT NULL,
        text TEXT NOT NULL,
        text_sha256 BYTEA NOT NULL,
        tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', heading_path || ' ' ||
        text)) STORED,
        CONSTRAINT pk_chunks PRIMARY KEY (id),
        CONSTRAINT uq_chunks_doc_version_id_block UNIQUE (doc_version_id, block),
        CONSTRAINT ck_chunks_kind CHECK (kind in ('text','table','slide','sheet')),
        CONSTRAINT ck_chunks_text_sha256 CHECK (octet_length(text_sha256) = 32),
        CONSTRAINT fk_chunks_doc_version_id_doc_versions FOREIGN KEY(doc_version_id)
        REFERENCES doc_versions (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)
    """)
    op.execute("""
        GRANT SELECT ON chunks TO craft_app
    """)
    op.execute("""
        CREATE TABLE vectors (
        id UUID DEFAULT uuidv7() NOT NULL,
        owner_kind TEXT NOT NULL,
        owner_id UUID NOT NULL,
        model TEXT NOT NULL,
        recipe TEXT NOT NULL,
        embedding VECTOR(1536) NOT NULL,
        CONSTRAINT pk_vectors PRIMARY KEY (id),
        CONSTRAINT uq_vectors_owner_kind_owner_id UNIQUE (owner_kind, owner_id),
        CONSTRAINT ck_vectors_owner_kind CHECK (owner_kind in
        ('chunk','doc_version','facet_text'))
        )
    """)
    op.execute("""
        CREATE INDEX ix_vectors_embedding ON vectors USING hnsw (embedding
        vector_cosine_ops)
    """)
    op.execute("""
        GRANT SELECT ON vectors TO craft_app
    """)
    op.execute("""
        CREATE TABLE version_diffs (
        from_version UUID NOT NULL,
        to_version UUID NOT NULL,
        seq INTEGER NOT NULL,
        change TEXT NOT NULL,
        from_block INTEGER,
        to_block INTEGER,
        CONSTRAINT pk_version_diffs PRIMARY KEY (from_version, to_version, seq),
        CONSTRAINT ck_version_diffs_change CHECK (change in
        ('added','removed','changed','moved')),
        CONSTRAINT fk_version_diffs_from_version_doc_versions FOREIGN KEY(from_version)
        REFERENCES doc_versions (id) ON DELETE CASCADE,
        CONSTRAINT fk_version_diffs_to_version_doc_versions FOREIGN KEY(to_version)
        REFERENCES doc_versions (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        GRANT SELECT ON version_diffs TO craft_app
    """)
    op.execute("""
        CREATE TABLE index_cursor (
        provider TEXT NOT NULL,
        cursor BIGINT NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_index_cursor PRIMARY KEY (provider)
        )
    """)
    op.execute("""
        GRANT SELECT ON index_cursor TO craft_app
    """)
    op.execute("""
        CREATE TABLE mission_grants (
        id UUID DEFAULT uuidv7() NOT NULL,
        user_id UUID NOT NULL,
        mission_key TEXT NOT NULL,
        silo TEXT,
        granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_mission_grants PRIMARY KEY (id),
        CONSTRAINT uq_mission_grants_user_id_mission_key_silo UNIQUE NULLS NOT DISTINCT
        (user_id, mission_key, silo),
        CONSTRAINT fk_mission_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users
        (id)
        )
    """)
    op.execute("""
        GRANT SELECT, INSERT, DELETE ON mission_grants TO craft_app
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE mission_grants
    """)
    op.execute("""
        DROP TABLE index_cursor
    """)
    op.execute("""
        DROP TABLE version_diffs
    """)
    op.execute("""
        DROP TABLE vectors
    """)
    op.execute("""
        DROP TABLE chunks
    """)
    op.execute("""
        DROP TABLE doc_versions
    """)
    op.execute("""
        DROP TABLE provider_items
    """)
