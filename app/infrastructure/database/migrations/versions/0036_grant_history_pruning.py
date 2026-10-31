"""Let the daily prune delete old traffic, queue samples and job errors. Application grants:
{'traffic_log': 'INSERT, DELETE, SELECT (at)', 'queue_samples': 'SELECT, INSERT, DELETE',
'job_attempt_errors': 'SELECT, INSERT, UPDATE, DELETE'}."""

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON traffic_log, queue_samples, job_attempt_errors TO craft_app")
    # Choosing which traffic rows are old reads their time, and nothing else.
    op.execute("GRANT SELECT (at) ON traffic_log TO craft_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT (at) ON traffic_log FROM craft_app")
    op.execute("REVOKE DELETE ON traffic_log, queue_samples, job_attempt_errors FROM craft_app")
