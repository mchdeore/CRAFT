"""Create cube records with explicit application grants."""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE facet_texts (
        id UUID DEFAULT uuidv7() NOT NULL,
        mission_key TEXT NOT NULL,
        facet TEXT NOT NULL,
        text TEXT NOT NULL,
        sources TEXT[] NOT NULL,
        model TEXT NOT NULL,
        built_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_facet_texts PRIMARY KEY (id),
        CONSTRAINT uq_facet_texts_mission_key_facet UNIQUE (mission_key, facet),
        CONSTRAINT ck_facet_texts_facet CHECK (facet in
        ('purpose','design','operations','program','cost_and_risk')),
        CONSTRAINT fk_facet_texts_mission_key_missions FOREIGN KEY(mission_key) REFERENCES
        missions (key)
        )
    """)
    op.execute("GRANT SELECT ON facet_texts TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE facet_texts")
