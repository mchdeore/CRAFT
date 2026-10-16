"""Create library and file records with declared application grants."""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE workspace_files (
        id UUID DEFAULT uuidv7() NOT NULL,
        conversation_id UUID NOT NULL,
        name TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 BYTEA NOT NULL,
        bytes BYTEA,
        origin TEXT NOT NULL,
        state TEXT NOT NULL,
        simulated BOOLEAN NOT NULL,
        classification TEXT NOT NULL,
        status_detail TEXT,
        export_approval_id UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_workspace_files PRIMARY KEY (id),
        CONSTRAINT ck_workspace_files_origin CHECK (origin in ('upload','agent')),
        CONSTRAINT ck_workspace_files_state CHECK (state in
        ('uploading','processing','ready','failed','export-pending','exported')),
        CONSTRAINT ck_workspace_files_classification CHECK (classification in
        ('unclassified','protected-a','protected-b')),
        CONSTRAINT fk_workspace_files_conversation_id_conversations FOREIGN
        KEY(conversation_id) REFERENCES conversations (id),
        CONSTRAINT fk_workspace_files_export_approval_id_approval_requests FOREIGN
        KEY(export_approval_id) REFERENCES approval_requests (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_workspace_files_conversation_id_created_at ON workspace_files
        (conversation_id, created_at DESC)
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_files TO craft_app
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE workspace_files
    """)
