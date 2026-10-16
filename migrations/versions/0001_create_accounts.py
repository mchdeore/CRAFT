"""Create accounts. Grants: INSERT/UPDATE, with INSERT/DELETE for roles."""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE users (\n\tid UUID DEFAULT uuidv7() NOT NULL, \n\tuserna"
        "me TEXT NOT NULL, \n\tdisplay_name TEXT NOT NULL, \n\tdisabled_at TIM"
        "ESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFA"
        "ULT now() NOT NULL, \n\tCONSTRAINT pk_users PRIMARY KEY (id), \n\tCON"
        "STRAINT ck_users_lower_username CHECK (username = lower(username)"
        "), \n\tCONSTRAINT uq_users_username UNIQUE (username)\n)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON users TO craft_app")
    op.execute(
        "CREATE TABLE local_credentials (\n\tuser_id UUID NOT NULL, \n\tpasswo"
        "rd_hash TEXT NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFA"
        "ULT now() NOT NULL, \n\tCONSTRAINT pk_local_credentials PRIMARY KEY"
        " (user_id), \n\tCONSTRAINT fk_local_credentials_user_id_users FOREI"
        "GN KEY(user_id) REFERENCES users (id)\n)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON local_credentials TO craft_app")
    op.execute(
        "CREATE TABLE user_roles (\n\tuser_id UUID NOT NULL, \n\trole TEXT NOT"
        " NULL, \n\tgranted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NU"
        "LL, \n\tCONSTRAINT pk_user_roles PRIMARY KEY (user_id, role), \n\tCON"
        "STRAINT ck_user_roles_role CHECK (role in ('member','admin')), \n\t"
        "CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFER"
        "ENCES users (id)\n)"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON user_roles TO craft_app")
    op.execute(
        "CREATE TABLE auth_sessions (\n\tid UUID DEFAULT uuidv7() NOT NULL, "
        "\n\tuser_id UUID NOT NULL, \n\ttoken_sha256 BYTEA NOT NULL, \n\tcreated"
        "_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tlast_seen_"
        "at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tidle_expire"
        "s_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WI"
        "TH TIME ZONE NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tr"
        "evoked_reason TEXT, \n\tCONSTRAINT pk_auth_sessions PRIMARY KEY (id"
        "), \n\tCONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_i"
        "d) REFERENCES users (id), \n\tCONSTRAINT uq_auth_sessions_token_sha"
        "256 UNIQUE (token_sha256)\n)"
    )
    op.execute("CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON auth_sessions TO craft_app")


def downgrade() -> None:
    op.execute("DROP TABLE auth_sessions")
    op.execute("DROP TABLE user_roles")
    op.execute("DROP TABLE local_credentials")
    op.execute("DROP TABLE users")
