"""create model usage. Application grants: {'model_usage': 'SELECT, INSERT'}."""

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Append-only token counts, one row per model response; joins call_log by call_id.
    op.execute(
        """
        CREATE TABLE model_usage (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            at TIMESTAMP WITH TIME ZONE NOT NULL,
            call_id UUID NOT NULL,
            role TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            CONSTRAINT pk_model_usage PRIMARY KEY (id),
            CONSTRAINT ck_model_usage_role CHECK (role in ('main','check','embedding'))
        )
        """
    )
    op.execute("CREATE INDEX ix_model_usage_at ON model_usage (at)")
    # The app sums the spend before each model request and appends; it never changes a row.
    op.execute("GRANT SELECT, INSERT ON model_usage TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE model_usage")
