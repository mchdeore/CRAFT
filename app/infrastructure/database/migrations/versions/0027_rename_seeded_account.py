"""Rename the seeded member account, copied into the frozen dataset, to analyst.

Only the username and display name change; the account keeps its ID, grants and history.
"""

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels = None
depends_on = None

OLD, NEW = "demo", "analyst"


def rename(old: str, new: str) -> None:
    op.execute(
        f"""
        UPDATE users SET username = '{new}', display_name = initcap('{new}')
            WHERE username = '{old}'
              AND NOT EXISTS (SELECT FROM users WHERE username = '{new}');
        UPDATE id_accounts SET username = '{new}', name = initcap('{new}')
            WHERE username = '{old}'
              AND NOT EXISTS (SELECT FROM id_accounts WHERE username = '{new}');
        """
    )


def upgrade() -> None:
    rename(OLD, NEW)


def downgrade() -> None:
    rename(NEW, OLD)
