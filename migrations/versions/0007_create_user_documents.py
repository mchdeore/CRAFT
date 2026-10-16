"""Create library and file records with declared application grants."""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drafts (
        id UUID DEFAULT uuidv7() NOT NULL,
        owner_id UUID NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        based_on UUID,
        approval_request_id UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_drafts PRIMARY KEY (id),
        CONSTRAINT fk_drafts_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id),
        CONSTRAINT fk_drafts_based_on_drafts FOREIGN KEY(based_on) REFERENCES drafts (id),
        CONSTRAINT fk_drafts_approval_request_id_approval_requests FOREIGN
        KEY(approval_request_id) REFERENCES approval_requests (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_drafts_owner_id_created_at ON drafts (owner_id, created_at DESC)
    """)
    op.execute("""
        GRANT SELECT, INSERT ON drafts TO craft_app
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE drafts
    """)
