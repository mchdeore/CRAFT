"""Create durable records with table-specific application grants."""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE agent_runs (
        id UUID DEFAULT uuidv7() NOT NULL,
        conversation_id UUID NOT NULL,
        started_by UUID NOT NULL,
        status TEXT NOT NULL,
        model TEXT NOT NULL,
        instructions_version TEXT NOT NULL,
        token_sha256 BYTEA,
        token_expires_at TIMESTAMP WITH TIME ZONE,
        started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        ended_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_agent_runs PRIMARY KEY (id),
        CONSTRAINT ck_agent_runs_status CHECK (status in
        ('running','paused','finished','failed','cancelled')),
        CONSTRAINT fk_agent_runs_conversation_id_conversations FOREIGN KEY(conversation_id)
        REFERENCES conversations (id),
        CONSTRAINT fk_agent_runs_started_by_users FOREIGN KEY(started_by) REFERENCES users
        (id),
        CONSTRAINT uq_agent_runs_token_sha256 UNIQUE (token_sha256)
        )
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON agent_runs TO craft_app
    """)
    op.execute("""
        CREATE TABLE agent_sessions (
        conversation_id UUID NOT NULL,
        state JSONB NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_agent_sessions PRIMARY KEY (conversation_id),
        CONSTRAINT fk_agent_sessions_conversation_id_conversations FOREIGN
        KEY(conversation_id) REFERENCES conversations (id)
        )
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON agent_sessions TO craft_app
    """)

    op.execute("""
        ALTER TABLE messages ADD CONSTRAINT fk_messages_run_id_agent_runs FOREIGN KEY
        (run_id) REFERENCES agent_runs (id)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE messages DROP CONSTRAINT fk_messages_run_id_agent_runs
    """)
    op.execute("""
        DROP TABLE agent_sessions
    """)
    op.execute("""
        DROP TABLE agent_runs
    """)
