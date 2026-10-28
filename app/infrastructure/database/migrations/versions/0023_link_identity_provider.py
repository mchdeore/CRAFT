"""Users carry their identity-provider key (tid, oid); unknown people leave an access request.

Accounts copied to craft-id in 0021 are linked here, so existing users sign in as before.
"""

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels = None
depends_on = None

CRAFT_ID_TENANT = "6f1c0e2a-163b-4c2a-9e0d-0c5a1d3b7e42"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE users
            ADD COLUMN idp_tenant_id UUID,
            ADD COLUMN idp_object_id UUID,
            ADD COLUMN email TEXT,
            ADD CONSTRAINT uq_users_idp_tenant_id_idp_object_id
                UNIQUE (idp_tenant_id, idp_object_id),
            ADD CONSTRAINT uq_users_email UNIQUE (email),
            ADD CONSTRAINT ck_users_lower_email CHECK (email = lower(email)),
            ADD CONSTRAINT ck_users_idp_key
                CHECK ((idp_tenant_id IS NULL) = (idp_object_id IS NULL));
        UPDATE users SET idp_tenant_id = '{CRAFT_ID_TENANT}', idp_object_id = id
            WHERE id IN (SELECT user_id FROM local_credentials);
        CREATE TABLE access_requests (
            tenant_id UUID NOT NULL,
            object_id UUID NOT NULL,
            email TEXT,
            name TEXT NOT NULL,
            first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_access_requests PRIMARY KEY (tenant_id, object_id)
        );
        CREATE TABLE sign_in_flows (
            id_sha256 BYTEA NOT NULL,
            flow JSONB NOT NULL,
            return_to TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_sign_in_flows PRIMARY KEY (id_sha256),
            CONSTRAINT ck_sign_in_flows_id_sha256 CHECK (octet_length(id_sha256) = 32)
        );
        GRANT SELECT, INSERT, UPDATE, DELETE ON access_requests TO craft_app;
        GRANT SELECT, INSERT, DELETE ON sign_in_flows TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE sign_in_flows;
        DROP TABLE access_requests;
        ALTER TABLE users
            DROP COLUMN email, DROP COLUMN idp_object_id, DROP COLUMN idp_tenant_id;
        """
    )
