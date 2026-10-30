"""A notebook grant records the approved operation: an edit grant covers edits, a run grant runs.

Existing grants take their approval's operation. One active grant per conversation, path and
operation, so approving a run of a notebook already granted for edits adds its own grant.
"""

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notebook_grants ADD COLUMN operation TEXT")
    op.execute(
        "UPDATE notebook_grants SET operation = approval_requests.operation "
        "FROM approval_requests WHERE approval_requests.id = notebook_grants.approval_id"
    )
    op.execute("ALTER TABLE notebook_grants ALTER COLUMN operation SET NOT NULL")
    op.execute("DROP INDEX uq_notebook_grants_active")
    op.execute(
        "CREATE UNIQUE INDEX uq_notebook_grants_active "
        "ON notebook_grants (conversation_id, path, operation) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    # Without the operation an edit grant would cover runs again, and a run grant edits, so
    # active grants are revoked: the agent asks again.
    op.execute("UPDATE notebook_grants SET revoked_at = now() WHERE revoked_at IS NULL")
    op.execute("DROP INDEX uq_notebook_grants_active")
    op.execute("ALTER TABLE notebook_grants DROP COLUMN operation")
    op.execute(
        "CREATE UNIQUE INDEX uq_notebook_grants_active "
        "ON notebook_grants (conversation_id, path) WHERE revoked_at IS NULL"
    )
