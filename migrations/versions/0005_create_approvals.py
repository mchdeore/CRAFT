"""Create durable records with table-specific application grants."""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE approval_requests (
        id UUID DEFAULT uuidv7() NOT NULL,
        run_id UUID NOT NULL,
        conversation_id UUID NOT NULL,
        requested_by UUID NOT NULL,
        operation TEXT NOT NULL,
        arguments JSONB NOT NULL,
        arguments_sha256 BYTEA NOT NULL,
        evidence JSONB NOT NULL,
        reasoning TEXT NOT NULL,
        retrieval_path JSONB NOT NULL,
        interrupt_id TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_approval_requests PRIMARY KEY (id),
        CONSTRAINT fk_approval_requests_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES
        agent_runs (id),
        CONSTRAINT fk_approval_requests_conversation_id_conversations FOREIGN
        KEY(conversation_id) REFERENCES conversations (id),
        CONSTRAINT fk_approval_requests_requested_by_users FOREIGN KEY(requested_by)
        REFERENCES users (id),
        CONSTRAINT uq_approval_requests_interrupt_id UNIQUE (interrupt_id)
        )
    """)
    op.execute("""
        GRANT SELECT, INSERT ON approval_requests TO craft_app
    """)
    op.execute("""
        CREATE TABLE approval_events (
        id UUID DEFAULT uuidv7() NOT NULL,
        request_id UUID NOT NULL,
        kind TEXT NOT NULL,
        actor_id UUID,
        at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        note TEXT,
        CONSTRAINT pk_approval_events PRIMARY KEY (id),
        CONSTRAINT ck_approval_events_kind CHECK (kind in
        ('requested','approved','rejected','executed','failed')),
        CONSTRAINT fk_approval_events_request_id_approval_requests FOREIGN KEY(request_id)
        REFERENCES approval_requests (id),
        CONSTRAINT fk_approval_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users
        (id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_approval_events_decision ON approval_events (request_id)
        WHERE kind in ('approved','rejected')
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_approval_events_execution ON approval_events (request_id)
        WHERE kind in ('executed','failed')
    """)
    op.execute("""
        GRANT SELECT, INSERT ON approval_events TO craft_app
    """)

    op.execute("""
        CREATE VIEW approval_status AS SELECT DISTINCT ON (request_id) request_id, kind,
        actor_id, at FROM approval_events ORDER BY request_id, at DESC, id DESC
    """)
    op.execute("""
        GRANT SELECT ON approval_status TO craft_app
    """)


def downgrade() -> None:
    op.execute("""
        DROP VIEW approval_status
    """)
    op.execute("""
        DROP TABLE approval_events
    """)
    op.execute("""
        DROP TABLE approval_requests
    """)
