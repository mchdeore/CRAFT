"""Private SQLite schema for the isolated prototype."""

import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    remote_id TEXT,
    scope_id TEXT,
    name TEXT NOT NULL,
    source_location TEXT NOT NULL,
    mission TEXT NOT NULL,
    silo TEXT,
    stage TEXT,
    classification TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    storage_mode TEXT NOT NULL CHECK (storage_mode IN ('local_originals', 'external_originals')),
    remote_status TEXT NOT NULL DEFAULT 'active' CHECK (remote_status IN ('active', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, remote_id),
    CHECK ((provider = 'local' AND remote_id IS NULL AND scope_id IS NULL)
        OR (provider <> 'local' AND remote_id IS NOT NULL AND scope_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_documents_scope
    ON documents(provider, scope_id, remote_status, remote_id);
CREATE INDEX IF NOT EXISTS idx_documents_mission
    ON documents(mission, classification, silo, stage);

CREATE TABLE IF NOT EXISTS doc_versions (
    version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    provider_version TEXT,
    source_key TEXT NOT NULL UNIQUE,
    storage_mode TEXT NOT NULL CHECK (storage_mode IN ('local_originals', 'external_originals')),
    blob_relative_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (document_id, sequence),
    UNIQUE (document_id, version_id),
    CHECK ((storage_mode = 'local_originals' AND blob_relative_path IS NOT NULL)
        OR (storage_mode = 'external_originals' AND blob_relative_path IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_doc_versions_hash
    ON doc_versions(document_id, content_hash, sequence);

CREATE TABLE IF NOT EXISTS provider_version_map (
    document_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    version_id TEXT NOT NULL,
    PRIMARY KEY (document_id, provider_version),
    FOREIGN KEY (document_id, version_id)
        REFERENCES doc_versions(document_id, version_id)
);

CREATE TABLE IF NOT EXISTS extractions (
    extraction_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    method TEXT NOT NULL,
    output_hash TEXT NOT NULL CHECK (length(output_hash) = 64),
    created_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    UNIQUE (version_id, method, output_hash),
    UNIQUE (version_id, extraction_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extractions_one_current
    ON extractions(version_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS blocks (
    block_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    extraction_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    location TEXT NOT NULL,
    heading TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL CHECK (length(text_hash) = 64),
    source_key TEXT NOT NULL UNIQUE,
    UNIQUE (extraction_id, ordinal),
    UNIQUE (version_id, block_id),
    FOREIGN KEY (version_id, extraction_id)
        REFERENCES extractions(version_id, extraction_id)
);
CREATE INDEX IF NOT EXISTS idx_blocks_version ON blocks(version_id, ordinal);

CREATE TABLE IF NOT EXISTS representations (
    representation_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    extraction_id TEXT NOT NULL,
    block_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN (
        'body_chunk', 'document_summary', 'section_summary', 'facet',
        'table_parameter', 'relationship_description'
    )),
    purpose TEXT NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL CHECK (length(text_hash) = 64),
    source_key TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL CHECK (origin IN ('extracted', 'generated', 'explicit', 'suggested')),
    generator_model TEXT,
    recipe TEXT,
    created_at TEXT,
    UNIQUE (version_id, representation_id),
    FOREIGN KEY (version_id, extraction_id)
        REFERENCES extractions(version_id, extraction_id),
    FOREIGN KEY (version_id, block_id)
        REFERENCES blocks(version_id, block_id)
);
CREATE INDEX IF NOT EXISTS idx_representations_kind
    ON representations(kind, version_id, extraction_id);

CREATE TABLE IF NOT EXISTS embedding_payloads (
    payload_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    input_type TEXT NOT NULL,
    recipe TEXT NOT NULL,
    text_hash TEXT NOT NULL CHECK (length(text_hash) = 64),
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (model, dimensions, input_type, recipe, text_hash),
    UNIQUE (payload_id, model, dimensions, recipe)
);

CREATE TABLE IF NOT EXISTS embeddings (
    embedding_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL REFERENCES representations(representation_id),
    payload_id TEXT NOT NULL REFERENCES embedding_payloads(payload_id),
    source_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (representation_id, payload_id),
    UNIQUE (representation_id, embedding_id)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_representation
    ON embeddings(representation_id, payload_id);

CREATE TABLE IF NOT EXISTS mission_grants (
    principal TEXT NOT NULL,
    mission TEXT NOT NULL,
    can_read INTEGER NOT NULL CHECK (can_read IN (0, 1)),
    PRIMARY KEY (principal, mission)
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    assignment_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    version_id TEXT,
    label_key TEXT NOT NULL,
    label_value TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('explicit', 'rule', 'accepted_model')),
    evidence_block_id TEXT,
    assigned_at TEXT NOT NULL,
    UNIQUE (document_id, version_id, label_key, label_value),
    FOREIGN KEY (document_id, version_id)
        REFERENCES doc_versions(document_id, version_id),
    FOREIGN KEY (version_id, evidence_block_id)
        REFERENCES blocks(version_id, block_id)
);
CREATE INDEX IF NOT EXISTS idx_tag_assignments_lookup
    ON tag_assignments(label_key, label_value, document_id);

CREATE TABLE IF NOT EXISTS tag_proposals (
    proposal_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    version_id TEXT NOT NULL,
    label_key TEXT NOT NULL,
    label_value TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('rule', 'model')),
    model TEXT,
    recipe TEXT NOT NULL,
    evidence_block_id TEXT,
    confidence TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (document_id, version_id)
        REFERENCES doc_versions(document_id, version_id),
    FOREIGN KEY (version_id, evidence_block_id)
        REFERENCES blocks(version_id, block_id)
);
CREATE INDEX IF NOT EXISTS idx_tag_proposals_version
    ON tag_proposals(version_id, label_key, label_value);

CREATE TABLE IF NOT EXISTS cube_values (
    fact_id TEXT PRIMARY KEY,
    mission TEXT NOT NULL,
    metric TEXT NOT NULL,
    subject TEXT NOT NULL,
    value_state TEXT NOT NULL CHECK (value_state IN (
        'value', 'missing', 'withheld', 'unknown', 'not_applicable', 'withdrawn'
    )),
    amount_decimal TEXT,
    unit TEXT,
    event_time TEXT NOT NULL,
    known_at TEXT NOT NULL,
    source_version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    source_block_id TEXT NOT NULL,
    source_location TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (mission, metric, subject, event_time, known_at),
    CHECK ((value_state = 'value' AND amount_decimal IS NOT NULL AND unit IS NOT NULL)
        OR (value_state <> 'value' AND amount_decimal IS NULL)),
    FOREIGN KEY (source_version_id, source_block_id)
        REFERENCES blocks(version_id, block_id)
);
CREATE INDEX IF NOT EXISTS idx_cube_lookup
    ON cube_values(mission, metric, subject, event_time, known_at, fact_id);

CREATE TABLE IF NOT EXISTS candidate_batches (
    batch_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    candidate_count INTEGER NOT NULL CHECK (candidate_count > 0),
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS candidate_values (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES candidate_batches(batch_id),
    mission TEXT NOT NULL,
    metric TEXT NOT NULL,
    subject TEXT NOT NULL,
    amount_decimal TEXT NOT NULL,
    unit TEXT NOT NULL,
    event_time TEXT NOT NULL,
    source_version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    source_block_id TEXT NOT NULL,
    source_location TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    FOREIGN KEY (source_version_id, source_block_id)
        REFERENCES blocks(version_id, block_id)
);
CREATE INDEX IF NOT EXISTS idx_candidate_batch ON candidate_values(batch_id, candidate_id);

CREATE TABLE IF NOT EXISTS candidate_publications (
    candidate_id TEXT PRIMARY KEY REFERENCES candidate_values(candidate_id),
    fact_id TEXT NOT NULL UNIQUE REFERENCES cube_values(fact_id),
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS structural_edges (
    edge_id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    target_version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source_version_id, target_version_id, relation)
);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL,
    source_block_id TEXT NOT NULL,
    target_version_id TEXT NOT NULL REFERENCES doc_versions(version_id),
    relation TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_version_id, source_block_id)
        REFERENCES blocks(version_id, block_id),
    UNIQUE (source_version_id, source_block_id, target_version_id, relation)
);

CREATE TABLE IF NOT EXISTS similarity_candidates (
    edge_id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL,
    source_representation_id TEXT NOT NULL,
    source_embedding_id TEXT NOT NULL,
    target_version_id TEXT NOT NULL,
    target_representation_id TEXT NOT NULL,
    target_embedding_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    recipe TEXT NOT NULL,
    score REAL NOT NULL CHECK (score >= -1.0 AND score <= 1.0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_version_id, source_representation_id)
        REFERENCES representations(version_id, representation_id),
    FOREIGN KEY (source_representation_id, source_embedding_id)
        REFERENCES embeddings(representation_id, embedding_id),
    FOREIGN KEY (target_version_id, target_representation_id)
        REFERENCES representations(version_id, representation_id),
    FOREIGN KEY (target_representation_id, target_embedding_id)
        REFERENCES embeddings(representation_id, embedding_id),
    UNIQUE (source_embedding_id, target_embedding_id, recipe)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    seen_count INTEGER NOT NULL CHECK (seen_count >= 0),
    change_count INTEGER NOT NULL CHECK (change_count >= 0),
    error_type TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL REFERENCES scan_runs(scan_id),
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    change_type TEXT NOT NULL CHECK (change_type IN (
        'added', 'renamed', 'moved', 'new_version', 'version_alias',
        'deleted', 'restored', 'metadata'
    )),
    source_version_id TEXT REFERENCES doc_versions(version_id),
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_similarity_payload_contract
BEFORE INSERT ON similarity_candidates
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM embeddings se
        JOIN embedding_payloads sp ON sp.payload_id = se.payload_id
        WHERE se.embedding_id = NEW.source_embedding_id
          AND se.representation_id = NEW.source_representation_id
          AND sp.model = NEW.model
          AND sp.dimensions = NEW.dimensions
          AND sp.recipe = NEW.recipe
    ) THEN RAISE(ABORT, 'source embedding contract mismatch') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM embeddings te
        JOIN embedding_payloads tp ON tp.payload_id = te.payload_id
        WHERE te.embedding_id = NEW.target_embedding_id
          AND te.representation_id = NEW.target_representation_id
          AND tp.model = NEW.model
          AND tp.dimensions = NEW.dimensions
          AND tp.recipe = NEW.recipe
    ) THEN RAISE(ABORT, 'target embedding contract mismatch') END;
END;
"""


def migrate_representation_times(connection: sqlite3.Connection) -> None:
    """Preserve legacy rows and backfill only known automatic extraction times."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(representations)")}
    if "created_at" in columns:
        return
    connection.execute("ALTER TABLE representations ADD COLUMN created_at TEXT")
    # These source keys identify body rows inserted with their extraction. Other
    # legacy representations may have been added later, even to an old extraction.
    connection.execute(
        """
        UPDATE representations SET created_at = (
            SELECT x.created_at FROM extractions x
            JOIN blocks b ON b.version_id = x.version_id AND b.extraction_id = x.extraction_id
            WHERE x.extraction_id = representations.extraction_id
              AND b.block_id = representations.block_id
              AND representations.source_key =
                  b.source_key || '/representation:body_chunk:body_heading_v1'
        )
        WHERE origin = 'extracted' AND kind = 'body_chunk' AND purpose = 'body_heading_v1'
        """
    )
