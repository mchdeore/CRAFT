"""A requester's standing grant to edit one notebook in one conversation without asking again."""

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notebook_grants (
            id UUID DEFAULT uuidv7() NOT NULL,
            approval_id UUID NOT NULL,
            user_id UUID NOT NULL,
            conversation_id UUID NOT NULL,
            path TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT pk_notebook_grants PRIMARY KEY (id),
            CONSTRAINT uq_notebook_grants_approval_id UNIQUE (approval_id),
            CONSTRAINT fk_notebook_grants_approval_id_approval_requests
                FOREIGN KEY(approval_id) REFERENCES approval_requests (id),
            CONSTRAINT fk_notebook_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
            CONSTRAINT fk_notebook_grants_conversation_id_conversations
                FOREIGN KEY(conversation_id) REFERENCES conversations (id)
        );
        CREATE UNIQUE INDEX uq_notebook_grants_active ON notebook_grants (conversation_id, path)
            WHERE revoked_at IS NULL;
        GRANT SELECT, INSERT, UPDATE ON notebook_grants TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE notebook_grants")
