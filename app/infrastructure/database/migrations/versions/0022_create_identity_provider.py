"""craft-id's tables, readable only by craft_id; existing passwords copied so they keep working.

Each account's oid is the CRAFT user's id, so 0022 can link the two without an email.
The craft_id login comes from initdb.sh; on a database created before it, grants wait for it.
"""

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels = None
depends_on = None

TABLES = "id_accounts, id_credentials, id_clients, id_codes"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE id_accounts (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            disabled_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_id_accounts PRIMARY KEY (id),
            CONSTRAINT uq_id_accounts_username UNIQUE (username),
            CONSTRAINT ck_id_accounts_lower_username CHECK (username = lower(username))
        );
        CREATE TABLE id_credentials (
            account_id UUID NOT NULL,
            password_hash TEXT NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_id_credentials PRIMARY KEY (account_id),
            CONSTRAINT fk_id_credentials_account_id_id_accounts
                FOREIGN KEY(account_id) REFERENCES id_accounts (id)
        );
        CREATE TABLE id_clients (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            name TEXT NOT NULL,
            secret_sha256 BYTEA NOT NULL,
            redirect_uris TEXT[] NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_id_clients PRIMARY KEY (id),
            CONSTRAINT ck_id_clients_secret_sha256 CHECK (octet_length(secret_sha256) = 32)
        );
        CREATE TABLE id_codes (
            code_sha256 BYTEA NOT NULL,
            client_id UUID NOT NULL,
            account_id UUID NOT NULL,
            redirect_uri TEXT NOT NULL,
            scope TEXT NOT NULL,
            nonce TEXT,
            code_challenge TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT pk_id_codes PRIMARY KEY (code_sha256),
            CONSTRAINT fk_id_codes_client_id_id_clients
                FOREIGN KEY(client_id) REFERENCES id_clients (id),
            CONSTRAINT fk_id_codes_account_id_id_accounts
                FOREIGN KEY(account_id) REFERENCES id_accounts (id),
            CONSTRAINT ck_id_codes_code_sha256 CHECK (octet_length(code_sha256) = 32)
        );
        INSERT INTO id_accounts (id, username, name, disabled_at, created_at)
            SELECT u.id, u.username, u.display_name, u.disabled_at, u.created_at
            FROM users u JOIN local_credentials c ON c.user_id = u.id;
        INSERT INTO id_credentials (account_id, password_hash, updated_at)
            SELECT user_id, password_hash, updated_at FROM local_credentials;
        """
    )
    # The default privileges give craft_app SELECT on new tables; it must not read these.
    op.execute(f"REVOKE ALL ON {TABLES} FROM craft_app")
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'craft_id') THEN
                GRANT USAGE ON SCHEMA public TO craft_id;
                GRANT SELECT, INSERT, UPDATE ON id_accounts, id_credentials, id_clients TO craft_id;
                GRANT SELECT, INSERT, UPDATE, DELETE ON id_codes TO craft_id;
            ELSE
                RAISE NOTICE 'craft_id role missing: craft-id cannot connect until it exists';
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE {', '.join(reversed(TABLES.split(', ')))}")
