"""Recover grouping for databases stamped by the earlier OCR-only 0016."""

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "group_name" not in {column["name"] for column in inspector.get_columns("conversations")}:
        op.add_column("conversations", sa.Column("group_name", sa.Text(), nullable=True))
    if "ck_conversations_group_name" not in {
        constraint["name"] for constraint in inspector.get_check_constraints("conversations")
    }:
        op.create_check_constraint(
            op.f("ck_conversations_group_name"),
            "conversations",
            "group_name is null or char_length(group_name) between 1 and 80",
        )


def downgrade() -> None:
    # Grouping belongs to 0016; rolling back this repair must preserve its data.
    pass
