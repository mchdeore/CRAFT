"""The table registry: every feature's tables register here (from S1). Alembic compares to it."""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def register_tables() -> None:
    """Import the table declarations without opening a connection."""
    from importlib import import_module

    for module in (
        "app.accounts.storage",
        "app.infrastructure.operations.storage",
        "app.conversations.storage",
        "app.agent.storage",
        "app.approvals.storage",
    ):
        import_module(module)


register_tables()


# Library and file tables have no storage-side transaction lifecycle.


def identifier() -> Column:
    return Column("id", UUID, primary_key=True, server_default=text("uuidv7()"))


def timestamp(name: str) -> Column:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=text("now()"))


provider_items = Table(
    "provider_items",
    metadata,
    Column("item", Text, primary_key=True),
    Column("path", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("mission_key", Text),
    Column("silo", Text),
    Column("stage", Text),
    Column("simulated", Boolean, nullable=False),
    Column("classification", Text, nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    timestamp("updated_at"),
    CheckConstraint("item ~ '^itm_[0-9a-f]{12}$'", name="item"),
    CheckConstraint(
        "classification in ('unclassified','protected-a','protected-b')", name="classification"
    ),
)
doc_versions = Table(
    "doc_versions",
    metadata,
    identifier(),
    Column("item", Text, ForeignKey("provider_items.item"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("sha256", LargeBinary, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("summary_kind", Text, nullable=False),
    Column("summary_tsv", TSVECTOR, Computed("to_tsvector('english', summary)", persisted=True)),
    timestamp("indexed_at"),
    UniqueConstraint("item", "version"),
    CheckConstraint("octet_length(sha256) = 32", name="sha256"),
    CheckConstraint("summary_kind in ('extract','generated')", name="summary_kind"),
)
Index("ix_doc_versions_summary_tsv", doc_versions.c.summary_tsv, postgresql_using="gin")
chunks = Table(
    "chunks",
    metadata,
    identifier(),
    Column(
        "doc_version_id", UUID, ForeignKey("doc_versions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("block", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("heading_path", Text, nullable=False),
    Column("locator", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("text_sha256", LargeBinary, nullable=False),
    Column(
        "tsv",
        TSVECTOR,
        Computed("to_tsvector('english', heading_path || ' ' || text)", persisted=True),
    ),
    UniqueConstraint("doc_version_id", "block"),
    CheckConstraint("kind in ('text','table','slide','sheet')", name="kind"),
    CheckConstraint("octet_length(text_sha256) = 32", name="text_sha256"),
)
Index("ix_chunks_tsv", chunks.c.tsv, postgresql_using="gin")
vectors = Table(
    "vectors",
    metadata,
    identifier(),
    Column("owner_kind", Text, nullable=False),
    Column("owner_id", UUID, nullable=False),
    Column("model", Text, nullable=False),
    Column("recipe", Text, nullable=False),
    Column("embedding", VECTOR(1536), nullable=False),
    UniqueConstraint("owner_kind", "owner_id"),
    CheckConstraint("owner_kind in ('chunk','doc_version','facet_text')", name="owner_kind"),
)
Index(
    "ix_vectors_embedding",
    vectors.c.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
version_diffs = Table(
    "version_diffs",
    metadata,
    Column(
        "from_version", UUID, ForeignKey("doc_versions.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("to_version", UUID, ForeignKey("doc_versions.id", ondelete="CASCADE"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("change", Text, nullable=False),
    Column("from_block", Integer),
    Column("to_block", Integer),
    CheckConstraint("change in ('added','removed','changed','moved')", name="change"),
)
index_cursor = Table(
    "index_cursor",
    metadata,
    Column("provider", Text, primary_key=True),
    Column("cursor", BigInteger, nullable=False),
    timestamp("updated_at"),
)
mission_grants = Table(
    "mission_grants",
    metadata,
    identifier(),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("mission_key", Text, nullable=False),
    Column("silo", Text),
    timestamp("granted_at"),
    UniqueConstraint("user_id", "mission_key", "silo", postgresql_nulls_not_distinct=True),
)
drafts = Table(
    "drafts",
    metadata,
    identifier(),
    Column("owner_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("based_on", UUID, ForeignKey("drafts.id")),
    Column("approval_request_id", UUID, ForeignKey("approval_requests.id")),
    timestamp("created_at"),
)
Index("ix_drafts_owner_id_created_at", drafts.c.owner_id, drafts.c.created_at.desc())
workspace_files = Table(
    "workspace_files",
    metadata,
    identifier(),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", LargeBinary, nullable=False),
    Column("bytes", LargeBinary),
    Column("origin", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("simulated", Boolean, nullable=False),
    Column("classification", Text, nullable=False),
    Column("status_detail", Text),
    Column("export_approval_id", UUID, ForeignKey("approval_requests.id")),
    timestamp("created_at"),
    CheckConstraint("origin in ('upload','agent')", name="origin"),
    CheckConstraint(
        "state in ('uploading','processing','ready','failed','export-pending','exported')",
        name="state",
    ),
    CheckConstraint(
        "classification in ('unclassified','protected-a','protected-b')", name="classification"
    ),
)
Index(
    "ix_workspace_files_conversation_id_created_at",
    workspace_files.c.conversation_id,
    workspace_files.c.created_at.desc(),
)
