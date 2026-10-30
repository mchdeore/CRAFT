"""Index approval status lookups, owned approval lists and per-conversation run filters."""

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Each status subquery reads one request's newest event; the approval_status view uses it too.
    op.execute(
        "CREATE INDEX ix_approval_events_request_id_at "
        "ON approval_events (request_id, at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_approval_requests_requested_by_created_at "
        "ON approval_requests (requested_by, created_at DESC, id DESC)"
    )
    op.execute("CREATE INDEX ix_approval_requests_run_id ON approval_requests (run_id)")
    op.execute(
        "CREATE INDEX ix_agent_runs_conversation_id_active ON agent_runs (conversation_id) "
        "WHERE status IN ('running', 'paused')"
    )
    op.execute(
        "CREATE INDEX ix_agent_runs_conversation_id_started_at "
        "ON agent_runs (conversation_id, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_agent_runs_conversation_id_started_at")
    op.execute("DROP INDEX ix_agent_runs_conversation_id_active")
    op.execute("DROP INDEX ix_approval_requests_run_id")
    op.execute("DROP INDEX ix_approval_requests_requested_by_created_at")
    op.execute("DROP INDEX ix_approval_events_request_id_at")
