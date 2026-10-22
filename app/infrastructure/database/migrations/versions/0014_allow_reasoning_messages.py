"""Keep opaque framework reasoning messages in owned conversation history."""

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_messages_role"), "messages", type_="check")
    op.create_check_constraint(
        op.f("ck_messages_role"),
        "messages",
        "role in ('user','assistant','tool','activity','reasoning')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_messages_role"), "messages", type_="check")
    op.create_check_constraint(
        op.f("ck_messages_role"), "messages", "role in ('user','assistant','tool','activity')"
    )
