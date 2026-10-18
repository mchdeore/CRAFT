"""Create owned skill heads and append-only definitions with stable mutation identity."""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE skills (
          id UUID DEFAULT uuidv7() NOT NULL,
          owner UUID NOT NULL,
          revision INTEGER DEFAULT 0 NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
          CONSTRAINT pk_skills PRIMARY KEY (id),
          CONSTRAINT uq_skills_id_owner UNIQUE (id, owner),
          CONSTRAINT ck_skills_revision CHECK (revision >= 0),
          CONSTRAINT fk_skills_owner_users FOREIGN KEY (owner) REFERENCES users(id)
        )
    """)
    op.execute("""
        CREATE TABLE skill_revisions (
          skill_id UUID NOT NULL,
          owner UUID NOT NULL,
          revision INTEGER NOT NULL,
          mutation_id UUID NOT NULL,
          request_hash TEXT NOT NULL,
          name TEXT NOT NULL,
          description TEXT NOT NULL,
          instructions TEXT NOT NULL,
          example_inputs TEXT[] NOT NULL,
          tool_names TEXT[] NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
          CONSTRAINT pk_skill_revisions PRIMARY KEY (skill_id, revision),
          CONSTRAINT uq_skill_revisions_owner_mutation_id UNIQUE (owner, mutation_id),
          CONSTRAINT fk_skill_revisions_skill_id_owner_skills FOREIGN KEY (skill_id, owner)
            REFERENCES skills(id, owner),
          CONSTRAINT ck_skill_revisions_revision CHECK (revision > 0),
          CONSTRAINT ck_skill_revisions_request_hash CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_skill_revisions_name CHECK (char_length(name) BETWEEN 1 AND 200),
          CONSTRAINT ck_skill_revisions_description CHECK (char_length(description) <= 2000),
          CONSTRAINT ck_skill_revisions_instructions CHECK (
            char_length(instructions) BETWEEN 1 AND 100000),
          CONSTRAINT ck_skill_revisions_example_inputs CHECK (cardinality(example_inputs) <= 20),
          CONSTRAINT ck_skill_revisions_tool_names CHECK (cardinality(tool_names) <= 100)
        )
    """)
    op.execute("CREATE INDEX ix_skills_owner_id ON skills (owner, id)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON skills TO craft_app")
    op.execute("GRANT SELECT, INSERT ON skill_revisions TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE skill_revisions")
    op.execute("DROP TABLE skills")
