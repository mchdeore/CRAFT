"""Personal tokens get a kind: `workspace` reaches /api, `mcp` reaches /mcp.

Existing tokens become `workspace`. The longest lifetime is checked by kind, and call_log gets
the index that counts a token's recent calls for the MCP rate limit.
"""

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing tokens, and any insert that names no kind, are workspace tokens.
    op.execute("ALTER TABLE workspace_tokens ADD COLUMN kind TEXT NOT NULL DEFAULT 'workspace'")
    op.execute(
        "ALTER TABLE workspace_tokens ADD CONSTRAINT ck_workspace_tokens_kind "
        "CHECK (kind in ('workspace','mcp'))"
    )
    op.execute(
        "ALTER TABLE workspace_tokens ADD CONSTRAINT ck_workspace_tokens_lifetime "
        "CHECK (expires_at <= created_at + "
        "CASE kind WHEN 'mcp' THEN interval '720 hours' ELSE interval '168 hours' END)"
    )
    op.execute(
        "CREATE INDEX ix_call_log_workspace_token_id_at ON call_log (workspace_token_id, at)"
    )


def downgrade() -> None:
    # Without the kind an MCP token would pass as a workspace token at /api, so it is revoked,
    # and its expiry is cut to a workspace token's longest so a later upgrade's check holds.
    op.execute(
        "UPDATE workspace_tokens SET revoked_at = coalesce(revoked_at, now()), "
        "expires_at = least(expires_at, created_at + interval '168 hours') WHERE kind = 'mcp'"
    )
    op.execute("DROP INDEX ix_call_log_workspace_token_id_at")
    op.execute("ALTER TABLE workspace_tokens DROP CONSTRAINT ck_workspace_tokens_lifetime")
    op.execute("ALTER TABLE workspace_tokens DROP CONSTRAINT ck_workspace_tokens_kind")
    op.execute("ALTER TABLE workspace_tokens DROP COLUMN kind")
