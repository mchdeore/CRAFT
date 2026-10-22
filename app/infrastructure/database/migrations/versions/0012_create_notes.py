"""Create private working notes with explicit application write grants."""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notes (
        id UUID DEFAULT uuidv7() NOT NULL,
        owner UUID NOT NULL,
        conversation_id UUID,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_notes PRIMARY KEY (id),
        CONSTRAINT uq_notes_owner_title UNIQUE (owner, title),
        CONSTRAINT ck_notes_title CHECK (char_length(title) BETWEEN 1 AND 200),
        CONSTRAINT ck_notes_body CHECK (char_length(body) <= 100000),
        CONSTRAINT fk_notes_owner_users FOREIGN KEY (owner) REFERENCES users(id),
        CONSTRAINT fk_notes_conversation_id_conversations FOREIGN KEY (conversation_id)
          REFERENCES conversations(id)
        )
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notes TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE notes")
