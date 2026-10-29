"""Job progress, notifications, task-mode runs, and the daily prune's DELETE grants."""

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE job_progress (
            job_id bigint NOT NULL,
            done integer NOT NULL,
            total integer NOT NULL,
            note text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_job_progress PRIMARY KEY (job_id),
            CONSTRAINT ck_job_progress_counts CHECK (done >= 0 AND total >= 0)
        );
        GRANT SELECT, INSERT, UPDATE, DELETE ON job_progress TO craft_app;
        """
    )
    op.execute(
        """
        CREATE TABLE notifications (
            id uuid NOT NULL DEFAULT uuidv7(),
            user_id uuid NOT NULL,
            kind text NOT NULL,
            ref_kind text NOT NULL,
            ref_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            read_at timestamptz,
            CONSTRAINT pk_notifications PRIMARY KEY (id),
            CONSTRAINT fk_notifications_user_id_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT ck_notifications_kind CHECK (
                kind IN ('job_done', 'job_failed', 'run_done', 'run_waiting')
            ),
            CONSTRAINT ck_notifications_ref_kind CHECK (ref_kind IN ('job', 'run'))
        );
        CREATE INDEX ix_notifications_user_id_id ON notifications (user_id, id);
        GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO craft_app;
        """
    )
    op.execute(
        """
        ALTER TABLE agent_runs ADD COLUMN mode text NOT NULL DEFAULT 'chat';
        ALTER TABLE agent_runs
            ADD CONSTRAINT ck_agent_runs_mode CHECK (mode IN ('chat', 'task'));
        GRANT DELETE ON auth_sessions, workspace_tokens TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("REVOKE DELETE ON auth_sessions, workspace_tokens FROM craft_app")
    op.execute(
        """
        ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS ck_agent_runs_mode;
        ALTER TABLE agent_runs DROP COLUMN IF EXISTS mode;
        """
    )
    op.execute("DROP TABLE notifications")
    op.execute("DROP TABLE job_progress")
