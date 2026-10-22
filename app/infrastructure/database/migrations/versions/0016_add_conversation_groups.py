"""Support one optional user-defined group per conversation."""

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("group_name", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_conversations_group_name"),
        "conversations",
        "group_name is null or char_length(group_name) between 1 and 80",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_conversations_group_name"), "conversations", type_="check")
    op.drop_column("conversations", "group_name")
