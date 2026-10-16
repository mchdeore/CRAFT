"""Create durable records with table-specific application grants."""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE conversations (
        id UUID DEFAULT uuidv7() NOT NULL,
        owner_id UUID NOT NULL,
        title TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        archived_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_conversations PRIMARY KEY (id),
        CONSTRAINT fk_conversations_owner_id_users FOREIGN KEY(owner_id) REFERENCES users
        (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_conversations_owner_id_updated_at ON conversations (owner_id,
        updated_at DESC)
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON conversations TO craft_app
    """)
    op.execute("""
        CREATE TABLE messages (
        id UUID DEFAULT uuidv7() NOT NULL,
        conversation_id UUID NOT NULL,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        content JSONB NOT NULL,
        run_id UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_messages PRIMARY KEY (id),
        CONSTRAINT ck_messages_role CHECK (role in ('user','assistant','tool','activity')),
        CONSTRAINT uq_messages_conversation_id_seq UNIQUE (conversation_id, seq),
        CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY(conversation_id)
        REFERENCES conversations (id)
        )
    """)
    op.execute("""
        GRANT SELECT, INSERT ON messages TO craft_app
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE messages
    """)
    op.execute("""
        DROP TABLE conversations
    """)
