"""CRAFT's own revisions of a document: immutable full copies that point at their source version."""

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_revisions (
            id UUID DEFAULT uuidv7() NOT NULL,
            item TEXT NOT NULL,
            source_version INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            content_sha256 BYTEA NOT NULL,
            bytes BYTEA NOT NULL,
            author_id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_document_revisions PRIMARY KEY (id),
            CONSTRAINT uq_document_revisions_item_revision UNIQUE (item, revision),
            CONSTRAINT ck_document_revisions_content_sha256
                CHECK (octet_length(content_sha256) = 32),
            CONSTRAINT ck_document_revisions_numbers CHECK (source_version >= 1 AND revision >= 1),
            CONSTRAINT fk_document_revisions_author_id_users
                FOREIGN KEY(author_id) REFERENCES users (id)
        );
        GRANT SELECT, INSERT ON document_revisions TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE document_revisions")
