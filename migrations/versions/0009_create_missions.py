"""Create cube records with explicit application grants."""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE silos (
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        seq INTEGER NOT NULL,
        CONSTRAINT pk_silos PRIMARY KEY (key),
        CONSTRAINT uq_silos_seq UNIQUE (seq)
        )
    """)
    op.execute("GRANT SELECT ON silos TO craft_app")
    op.execute("""
        CREATE TABLE stages (
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        seq INTEGER NOT NULL,
        gate TEXT NOT NULL,
        CONSTRAINT pk_stages PRIMARY KEY (key),
        CONSTRAINT uq_stages_seq UNIQUE (seq)
        )
    """)
    op.execute("GRANT SELECT ON stages TO craft_app")
    op.execute("""
        CREATE TABLE missions (
        key TEXT NOT NULL,
        title TEXT NOT NULL,
        current_stage TEXT NOT NULL,
        simulated BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_missions PRIMARY KEY (key),
        CONSTRAINT ck_missions_key CHECK (key ~ '^msn-[a-z0-9-]{1,40}$'),
        CONSTRAINT fk_missions_current_stage_stages FOREIGN KEY(current_stage) REFERENCES stages
        (key)
        )
    """)
    op.execute("GRANT SELECT ON missions TO craft_app")
    op.execute("""
        CREATE TABLE mission_aliases (
        alias TEXT NOT NULL,
        mission_key TEXT NOT NULL,
        CONSTRAINT pk_mission_aliases PRIMARY KEY (alias),
        CONSTRAINT ck_mission_aliases_alias CHECK (alias = lower(alias)),
        CONSTRAINT fk_mission_aliases_mission_key_missions FOREIGN KEY(mission_key) REFERENCES
        missions (key)
        )
    """)
    op.execute("GRANT SELECT ON mission_aliases TO craft_app")
    op.execute("""
        CREATE TABLE elements (
        id UUID DEFAULT uuidv7() NOT NULL,
        mission_key TEXT NOT NULL,
        silo TEXT NOT NULL,
        name TEXT NOT NULL,
        parent_id UUID,
        CONSTRAINT pk_elements PRIMARY KEY (id),
        CONSTRAINT uq_elements_mission_key_silo_name UNIQUE (mission_key, silo, name),
        CONSTRAINT fk_elements_mission_key_missions FOREIGN KEY(mission_key) REFERENCES missions
        (key),
        CONSTRAINT fk_elements_silo_silos FOREIGN KEY(silo) REFERENCES silos (key),
        CONSTRAINT fk_elements_parent_id_elements FOREIGN KEY(parent_id) REFERENCES elements
        (id)
        )
    """)
    op.execute("GRANT SELECT ON elements TO craft_app")
    op.bulk_insert(
        sa.table(
            "silos",
            sa.column("key", sa.Text),
            sa.column("label", sa.Text),
            sa.column("seq", sa.Integer),
        ),
        [
            {"key": "requirements", "label": "Requirements", "seq": 1},
            {"key": "quality", "label": "Quality", "seq": 2},
            {"key": "launch", "label": "Launch", "seq": 3},
            {"key": "engineering", "label": "Engineering", "seq": 4},
            {"key": "reviews", "label": "Reviews", "seq": 5},
            {"key": "budget", "label": "Budget", "seq": 6},
        ],
    )
    op.bulk_insert(
        sa.table(
            "stages",
            sa.column("key", sa.Text),
            sa.column("label", sa.Text),
            sa.column("seq", sa.Integer),
            sa.column("gate", sa.Text),
        ),
        [
            {
                "key": "st1-concept",
                "label": "Concept",
                "seq": 1,
                "gate": "The mission need and options are agreed",
            },
            {
                "key": "st2-definition",
                "label": "Definition",
                "seq": 2,
                "gate": "System requirements are baselined",
            },
            {"key": "st3-design", "label": "Design", "seq": 3, "gate": "The design review passes"},
            {
                "key": "st4-build",
                "label": "Build",
                "seq": 4,
                "gate": "Units are built and integrated",
            },
            {
                "key": "st5-verification",
                "label": "Verification",
                "seq": 5,
                "gate": "The test campaign is complete",
            },
            {"key": "st6-operation", "label": "Operation", "seq": 6, "gate": "The mission ends"},
            {
                "key": "st7-closure",
                "label": "Closure",
                "seq": 7,
                "gate": "Disposal and lessons are recorded",
            },
        ],
    )


def downgrade() -> None:
    op.execute("DROP TABLE elements")
    op.execute("DROP TABLE mission_aliases")
    op.execute("DROP TABLE missions")
    op.execute("DROP TABLE stages")
    op.execute("DROP TABLE silos")
