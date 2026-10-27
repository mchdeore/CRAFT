"""Chat turns run on the agent worker: their inputs and the events a browser re-attaches to."""

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One segment per POST /api/agent/runs (the start, then each resume) of a run.
    op.execute(
        """
        CREATE TABLE agent_run_inputs (
            run_id UUID NOT NULL,
            segment INTEGER NOT NULL,
            owner_id UUID NOT NULL,
            input JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_agent_run_inputs PRIMARY KEY (run_id, segment),
            CONSTRAINT fk_agent_run_inputs_owner_id_users
                FOREIGN KEY(owner_id) REFERENCES users (id)
        );
        CREATE TABLE agent_run_events (
            run_id UUID NOT NULL,
            segment INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            event JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_agent_run_events PRIMARY KEY (run_id, segment, seq)
        );
        GRANT SELECT, INSERT, DELETE ON agent_run_inputs, agent_run_events TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE agent_run_events")
    op.execute("DROP TABLE agent_run_inputs")
