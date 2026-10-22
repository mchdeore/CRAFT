"""Per-account workspace tokens for read-only route access from owned Python."""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE workspace_tokens (\n\tid UUID DEFAULT uuidv7() NOT NULL, "
        "\n\tuser_id UUID NOT NULL, \n\ttoken_sha256 BYTEA NOT NULL, \n\tlabel TEXT NOT NULL, "
        "\n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, "
        "\n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tlast_used_at TIMESTAMP WITH TIME ZONE, \n\trevoked_at TIMESTAMP WITH TIME ZONE, "
        "\n\tCONSTRAINT pk_workspace_tokens PRIMARY KEY (id), "
        "\n\tCONSTRAINT ck_workspace_tokens_token_sha256 CHECK (octet_length(token_sha256) = 32), "
        "\n\tCONSTRAINT fk_workspace_tokens_user_id_users FOREIGN KEY(user_id) "
        "REFERENCES users (id), "
        "\n\tCONSTRAINT uq_workspace_tokens_token_sha256 UNIQUE (token_sha256)\n)"
    )
    op.execute("CREATE INDEX ix_workspace_tokens_user_id ON workspace_tokens (user_id)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON workspace_tokens TO craft_app")
    op.add_column("call_log", sa.Column("workspace_token_id", sa.UUID()))
    op.drop_constraint(op.f("ck_call_log_origin"), "call_log", type_="check")
    op.create_check_constraint(
        op.f("ck_call_log_origin"), "call_log", "origin in ('http','mcp','python','workspace')"
    )


def downgrade() -> None:
    # Audit rows stay: an existing workspace call makes this check, and the downgrade, fail.
    op.drop_constraint(op.f("ck_call_log_origin"), "call_log", type_="check")
    op.create_check_constraint(
        op.f("ck_call_log_origin"), "call_log", "origin in ('http','mcp','python')"
    )
    op.drop_column("call_log", "workspace_token_id")
    op.execute("DROP TABLE workspace_tokens")
