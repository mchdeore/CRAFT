"""Each user picks the language CRAFT replies in."""

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en'
            CONSTRAINT ck_users_language CHECK (language IN ('en', 'fr'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN language")
