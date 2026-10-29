"""create traffic log. Application grants: {'traffic_log': 'INSERT'}."""

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Append-only, no foreign keys; joins call_log by request_id. Byte counts, never content.
    op.execute(
        """
        CREATE TABLE traffic_log (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            at TIMESTAMP WITH TIME ZONE NOT NULL,
            request_id UUID,
            operation TEXT NOT NULL,
            method TEXT NOT NULL,
            status INTEGER,
            origin TEXT,
            user_id UUID,
            workspace_token_id UUID,
            bytes_in BIGINT NOT NULL,
            bytes_out BIGINT NOT NULL,
            duration_ms INTEGER NOT NULL,
            CONSTRAINT pk_traffic_log PRIMARY KEY (id)
        )
        """
    )
    op.execute("CREATE INDEX ix_traffic_log_at ON traffic_log (at)")
    # The app only writes; the owner login reads. Default privileges grant SELECT to craft_app.
    op.execute("REVOKE ALL ON traffic_log FROM craft_app")
    op.execute("GRANT INSERT ON traffic_log TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE traffic_log")
