"""Add optional OCR enrichment fields to indexed document versions."""

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # An earlier OCR-only revision used 0016, before conversation grouping.
    if "group_name" not in {column["name"] for column in inspector.get_columns("conversations")}:
        op.add_column("conversations", sa.Column("group_name", sa.Text(), nullable=True))
        op.create_check_constraint(
            op.f("ck_conversations_group_name"),
            "conversations",
            "group_name is null or char_length(group_name) between 1 and 80",
        )

    existing = {column["name"] for column in inspector.get_columns("doc_versions")}
    for column in (
        sa.Column("ocr_text", sa.Text()),
        sa.Column("ocr_model", sa.Text()),
        sa.Column("ocr_updated_at", sa.DateTime(timezone=True)),
    ):
        if column.name not in existing:
            op.add_column("doc_versions", column)


def downgrade() -> None:
    op.drop_column("doc_versions", "ocr_updated_at")
    op.drop_column("doc_versions", "ocr_model")
    op.drop_column("doc_versions", "ocr_text")
