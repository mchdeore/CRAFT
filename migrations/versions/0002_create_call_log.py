"""create call log. Application grants: {'call_log': 'SELECT, INSERT'}."""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE call_log (\n\tid BIGINT GENERATED ALWAYS AS IDENTITY, "
        "\n\tat TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tkind TEXT"
        " NOT NULL, \n\tname TEXT NOT NULL, \n\torigin TEXT NOT NULL, \n\toutcom"
        "e TEXT NOT NULL, \n\terror_code TEXT, \n\tduration_ms INTEGER NOT NUL"
        "L, \n\tdb_statements INTEGER DEFAULT 0 NOT NULL, \n\tdb_ms INTEGER DE"
        "FAULT 0 NOT NULL, \n\trequest_id UUID NOT NULL, \n\tcall_id UUID NOT "
        "NULL, \n\tparent_call_id UUID, \n\tuser_id UUID, \n\tauth_session_id UU"
        "ID, \n\tconversation_id UUID, \n\trun_id UUID, \n\ttool_call_id TEXT, \n"
        "\tCONSTRAINT pk_call_log PRIMARY KEY (id), \n\tCONSTRAINT ck_call_lo"
        "g_kind CHECK (kind in ('operation','step')), \n\tCONSTRAINT ck_call"
        "_log_origin CHECK (origin in ('http','mcp','python')), \n\tCONSTRAI"
        "NT ck_call_log_outcome CHECK (outcome in ('ok','error'))\n)"
    )
    op.execute("CREATE INDEX ix_call_log_at ON call_log (at)")
    op.execute("CREATE INDEX ix_call_log_request_id ON call_log (request_id)")
    op.execute("CREATE INDEX ix_call_log_run_id ON call_log (run_id)")
    op.execute("CREATE INDEX ix_call_log_user_id_at ON call_log (user_id, at)")
    op.execute("GRANT SELECT, INSERT ON call_log TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE call_log")
