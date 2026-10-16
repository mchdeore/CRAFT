"""Create cube records with explicit application grants."""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE parameters (
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        unit TEXT NOT NULL,
        min_value NUMERIC,
        max_value NUMERIC,
        whole BOOLEAN DEFAULT false NOT NULL,
        CONSTRAINT pk_parameters PRIMARY KEY (key),
        CONSTRAINT ck_parameters_key CHECK (key ~ '^[a-z][a-z0-9_]{1,40}$')
        )
    """)
    op.execute("GRANT SELECT ON parameters TO craft_app")
    op.execute("""
        CREATE TABLE unit_conversions (
        parameter TEXT NOT NULL,
        unit TEXT NOT NULL,
        multiplier NUMERIC NOT NULL,
        value_offset NUMERIC DEFAULT 0 NOT NULL,
        CONSTRAINT pk_unit_conversions PRIMARY KEY (parameter, unit),
        CONSTRAINT fk_unit_conversions_parameter_parameters FOREIGN KEY(parameter) REFERENCES
        parameters (key)
        )
    """)
    op.execute("GRANT SELECT ON unit_conversions TO craft_app")
    op.execute("""
        CREATE TABLE header_synonyms (
        header TEXT NOT NULL,
        parameter TEXT NOT NULL,
        language TEXT NOT NULL,
        CONSTRAINT pk_header_synonyms PRIMARY KEY (header, parameter),
        CONSTRAINT ck_header_synonyms_header CHECK (header = lower(header)),
        CONSTRAINT ck_header_synonyms_language CHECK (language in ('en','fr')),
        CONSTRAINT fk_header_synonyms_parameter_parameters FOREIGN KEY(parameter) REFERENCES
        parameters (key)
        )
    """)
    op.execute("GRANT SELECT ON header_synonyms TO craft_app")
    op.execute("""
        CREATE TABLE cube_values (
        id UUID DEFAULT uuidv7() NOT NULL,
        mission_key TEXT NOT NULL,
        silo TEXT NOT NULL,
        stage TEXT,
        element_id UUID,
        parameter TEXT NOT NULL,
        kind TEXT NOT NULL,
        low NUMERIC,
        high NUMERIC,
        source_number TEXT,
        source_unit TEXT,
        valid_from DATE NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        item TEXT NOT NULL,
        version INTEGER NOT NULL,
        block INTEGER NOT NULL,
        locator TEXT NOT NULL,
        quote_sha256 BYTEA NOT NULL,
        approval_request_id UUID,
        simulated BOOLEAN NOT NULL,
        CONSTRAINT pk_cube_values PRIMARY KEY (id),
        CONSTRAINT ck_cube_values_kind CHECK (kind in
        ('exact','range','unknown','not_applicable')),
        CONSTRAINT ck_cube_values_quote_sha256 CHECK (octet_length(quote_sha256) = 32),
        CONSTRAINT ck_cube_values_numbers CHECK ((kind in ('exact','range') and low is not null
        and high is not null) or (kind in ('unknown','not_applicable') and low is null and high
        is null)),
        CONSTRAINT ck_cube_values_exact CHECK (kind <> 'exact' or low = high),
        CONSTRAINT ck_cube_values_range CHECK (low is null or low <= high),
        CONSTRAINT fk_cube_values_mission_key_missions FOREIGN KEY(mission_key) REFERENCES
        missions (key),
        CONSTRAINT fk_cube_values_silo_silos FOREIGN KEY(silo) REFERENCES silos (key),
        CONSTRAINT fk_cube_values_stage_stages FOREIGN KEY(stage) REFERENCES stages (key),
        CONSTRAINT fk_cube_values_element_id_elements FOREIGN KEY(element_id) REFERENCES
        elements (id),
        CONSTRAINT fk_cube_values_parameter_parameters FOREIGN KEY(parameter) REFERENCES
        parameters (key),
        CONSTRAINT fk_cube_values_approval_request_id_approval_requests FOREIGN
        KEY(approval_request_id) REFERENCES approval_requests (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_cube_values_mission_key_parameter_valid_from_recorded_at ON cube_values
        (mission_key, parameter, valid_from DESC, recorded_at DESC)
    """)
    op.execute("GRANT SELECT, INSERT ON cube_values TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE cube_values")
    op.execute("DROP TABLE header_synonyms")
    op.execute("DROP TABLE unit_conversions")
    op.execute("DROP TABLE parameters")
