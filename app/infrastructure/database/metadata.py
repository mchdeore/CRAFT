"""The table registry: every feature's tables register here (from S1). Alembic compares to it."""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

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
        "app.agent.usage",
        "app.approvals.storage",
        "app.jobs.storage",
        "app.identity.storage",
        "app.notifications.storage",
        "app.infrastructure.traffic",
    ):
        import_module(module)


register_tables()


def include_name(name, type_, parent_names) -> bool:
    """Alembic compares only CRAFT's tables: Procrastinate owns its own (pinned SQL)."""
    return not (type_ == "table" and (name or "").startswith("procrastinate_"))


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
    Column("ocr_text", Text),
    Column("ocr_model", Text),
    Column("ocr_updated_at", DateTime(timezone=True)),
    Column(
        "summary_tsv",
        TSVECTOR,
        Computed("to_tsvector('english', summary)", persisted=True),
        nullable=False,
    ),
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
        nullable=False,
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
# CRAFT's own revisions: immutable full copies pointing at their source version (0025).
document_revisions = Table(
    "document_revisions",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("item", Text, nullable=False),
    Column("source_version", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("file_name", Text, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("content_sha256", LargeBinary, nullable=False),
    Column("bytes", LargeBinary, nullable=False),
    Column("author_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("item", "revision"),
    CheckConstraint("octet_length(content_sha256) = 32", name="content_sha256"),
    CheckConstraint("source_version >= 1 AND revision >= 1", name="numbers"),
)
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


silos = Table(
    "silos",
    metadata,
    Column("key", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("seq", Integer, nullable=False, unique=True),
)
stages = Table(
    "stages",
    metadata,
    Column("key", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("seq", Integer, nullable=False, unique=True),
    Column("gate", Text, nullable=False),
)
missions = Table(
    "missions",
    metadata,
    Column("key", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("current_stage", Text, ForeignKey("stages.key"), nullable=False),
    Column("simulated", Boolean, nullable=False),
    timestamp("created_at"),
    CheckConstraint("key ~ '^msn-[a-z0-9-]{1,40}$'", name="key"),
)
mission_aliases = Table(
    "mission_aliases",
    metadata,
    Column("alias", Text, primary_key=True),
    Column("mission_key", Text, ForeignKey("missions.key"), nullable=False),
    CheckConstraint("alias = lower(alias)", name="alias"),
)
elements = Table(
    "elements",
    metadata,
    identifier(),
    Column("mission_key", Text, ForeignKey("missions.key"), nullable=False),
    Column("silo", Text, ForeignKey("silos.key"), nullable=False),
    Column("name", Text, nullable=False),
    Column("parent_id", UUID, ForeignKey("elements.id")),
    UniqueConstraint("mission_key", "silo", "name"),
)
parameters = Table(
    "parameters",
    metadata,
    Column("key", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("unit", Text, nullable=False),
    Column("min_value", Numeric),
    Column("max_value", Numeric),
    Column("whole", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("key ~ '^[a-z][a-z0-9_]{1,40}$'", name="key"),
)
unit_conversions = Table(
    "unit_conversions",
    metadata,
    Column("parameter", Text, ForeignKey("parameters.key"), primary_key=True),
    Column("unit", Text, primary_key=True),
    Column("multiplier", Numeric, nullable=False),
    Column("value_offset", Numeric, nullable=False, server_default=text("0")),
)
header_synonyms = Table(
    "header_synonyms",
    metadata,
    Column("header", Text, primary_key=True),
    Column("parameter", Text, ForeignKey("parameters.key"), primary_key=True),
    Column("language", Text, nullable=False),
    CheckConstraint("header = lower(header)", name="header"),
    CheckConstraint("language in ('en','fr')", name="language"),
)
cube_values = Table(
    "cube_values",
    metadata,
    identifier(),
    Column("mission_key", Text, ForeignKey("missions.key"), nullable=False),
    Column("silo", Text, ForeignKey("silos.key"), nullable=False),
    Column("stage", Text, ForeignKey("stages.key")),
    Column("element_id", UUID, ForeignKey("elements.id")),
    Column("parameter", Text, ForeignKey("parameters.key"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("low", Numeric),
    Column("high", Numeric),
    Column("source_number", Text),
    Column("source_unit", Text),
    Column("valid_from", Date, nullable=False),
    timestamp("recorded_at"),
    Column("item", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("block", Integer, nullable=False),
    Column("locator", Text, nullable=False),
    Column("quote_sha256", LargeBinary, nullable=False),
    Column("approval_request_id", UUID, ForeignKey("approval_requests.id")),
    Column("simulated", Boolean, nullable=False),
    CheckConstraint("kind in ('exact','range','unknown','not_applicable')", name="kind"),
    CheckConstraint("octet_length(quote_sha256) = 32", name="quote_sha256"),
    CheckConstraint(
        "(kind in ('exact','range') and low is not null and high is not null) or "
        "(kind in ('unknown','not_applicable') and low is null and high is null)",
        name="numbers",
    ),
    CheckConstraint("kind <> 'exact' or low = high", name="exact"),
    CheckConstraint("low is null or low <= high", name="range"),
)
Index(
    "ix_cube_values_mission_key_parameter_valid_from_recorded_at",
    cube_values.c.mission_key,
    cube_values.c.parameter,
    cube_values.c.valid_from.desc(),
    cube_values.c.recorded_at.desc(),
)
facet_texts = Table(
    "facet_texts",
    metadata,
    identifier(),
    Column("mission_key", Text, ForeignKey("missions.key"), nullable=False),
    Column("facet", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("sources", ARRAY(Text), nullable=False),
    Column("model", Text, nullable=False),
    timestamp("built_at"),
    UniqueConstraint("mission_key", "facet"),
    CheckConstraint(
        "facet in ('purpose','design','operations','program','cost_and_risk')", name="facet"
    ),
)

notes = Table(
    "notes",
    metadata,
    identifier(),
    Column("owner", UUID, ForeignKey("users.id"), nullable=False),
    Column("conversation_id", UUID, ForeignKey("conversations.id")),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    timestamp("created_at"),
    timestamp("updated_at"),
    UniqueConstraint("owner", "title"),
    CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title"),
    CheckConstraint("char_length(body) <= 100000", name="body"),
)

skills = Table(
    "skills",
    metadata,
    identifier(),
    Column("owner", UUID, ForeignKey("users.id"), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("0")),
    timestamp("created_at"),
    UniqueConstraint("id", "owner"),
    CheckConstraint("revision >= 0", name="revision"),
)
Index("ix_skills_owner_id", skills.c.owner, skills.c.id)

skill_revisions = Table(
    "skill_revisions",
    metadata,
    Column("skill_id", UUID, primary_key=True),
    Column("owner", UUID, nullable=False),
    Column("revision", Integer, primary_key=True),
    Column("mutation_id", UUID, nullable=False),
    Column("request_hash", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("instructions", Text, nullable=False),
    Column("example_inputs", ARRAY(Text), nullable=False),
    Column("tool_names", ARRAY(Text), nullable=False),
    timestamp("created_at"),
    UniqueConstraint("owner", "mutation_id"),
    ForeignKeyConstraint(["skill_id", "owner"], ["skills.id", "skills.owner"]),
    CheckConstraint("revision > 0", name="revision"),
    CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
    CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name"),
    CheckConstraint("char_length(description) <= 2000", name="description"),
    CheckConstraint("char_length(instructions) BETWEEN 1 AND 100000", name="instructions"),
    CheckConstraint("cardinality(example_inputs) <= 20", name="example_inputs"),
    CheckConstraint("cardinality(tool_names) <= 100", name="tool_names"),
)
