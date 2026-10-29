"""craft-id's rules: who signs in, single-use codes with PKCE, and signed ID tokens."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.identity import storage
from app.infrastructure.access import now
from app.infrastructure.database.unit_of_work import unit_of_work

# craft-id serves one fake tenant, standing in for 163Oxygen's Entra tenant.
TENANT = UUID("6f1c0e2a-163b-4c2a-9e0d-0c5a1d3b7e42")
CODE_LIFETIME = timedelta(minutes=10)
TOKEN_LIFETIME = timedelta(hours=1)
SCOPES = {"openid", "profile", "email", "offline_access"}
PASSWORDS = PasswordHasher()
DUMMY = PASSWORDS.hash("unused-synthetic-dummy-password")


class Refused(Exception):
    """An OAuth error: `error` is the protocol code; nothing secret goes in it."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class Keys:
    """The signing key and its public half as a JWKS entry."""

    def __init__(self, pem: str = "") -> None:
        key = (
            serialization.load_pem_private_key(pem.encode(), password=None)
            if pem
            else rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SystemExit("bad setting: id_signing_key: invalid configuration")
        self.private = key
        numbers = self.private.public_key().public_numbers()

        def integer(value: int) -> str:
            return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))

        public = {"kty": "RSA", "e": integer(numbers.e), "n": integer(numbers.n)}
        # RFC 7638 thumbprint as the key ID.
        thumbprint = json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
        self.kid = b64(hashlib.sha256(thumbprint).digest())
        self.jwk = {**public, "use": "sig", "alg": "RS256", "kid": self.kid}

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})


def issuer(public_url: str) -> str:
    return f"{public_url}/id/{TENANT}/v2.0"


def check_client(sessions, client_id: str, redirect_uri: str):
    """The registered client, if the redirect URI is one of its own, exactly."""
    try:
        identifier = UUID(client_id)
    except ValueError:
        raise Refused("unauthorized_client") from None
    with unit_of_work(sessions, read_only=True) as session:
        client = storage.client(session, identifier)
    if client is None:
        raise Refused("unauthorized_client")
    if redirect_uri not in client["redirect_uris"]:
        raise Refused("invalid_request")
    return client


def authenticate(sessions, username: str, password: str):
    """The enabled account for these credentials, or None. Always verifies a hash."""
    with unit_of_work(sessions, read_only=True) as session:
        account = storage.account(session, username.strip().lower())
        digest = storage.password_hash(session, account["id"]) if account else None
    try:
        valid = PASSWORDS.verify(digest or DUMMY, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or account is None or digest is None or account["disabled_at"] is not None:
        return None
    return account


def issue_code(sessions, client, account, request: dict) -> str:
    code = b64(secrets.token_bytes(32))
    with unit_of_work(sessions) as session:
        session.execute(
            storage.id_codes.insert().values(
                code_sha256=sha256(code),
                client_id=client["id"],
                account_id=account["id"],
                redirect_uri=request["redirect_uri"],
                scope=request["scope"],
                nonce=request.get("nonce") or None,
                code_challenge=request["code_challenge"],
                expires_at=now() + CODE_LIFETIME,
            )
        )
    return code


def redeem(sessions, keys: Keys, public_url: str, form: dict, basic: tuple[str, str] | None):
    """Exchange a code for tokens: client secret, single use, expiry, redirect URI and PKCE."""
    client_id, secret = basic or (form.get("client_id", ""), form.get("client_secret", ""))
    if form.get("grant_type") != "authorization_code":
        raise Refused("unsupported_grant_type")
    try:
        identifier = UUID(client_id)
    except ValueError:
        raise Refused("invalid_client") from None
    code, verifier = form.get("code", ""), form.get("code_verifier", "")
    with unit_of_work(sessions) as session:
        client = storage.client(session, identifier)
        if client is None or not hmac.compare_digest(client["secret_sha256"], sha256(secret)):
            raise Refused("invalid_client")
        row = (
            session.execute(
                update(storage.id_codes)
                .where(
                    storage.id_codes.c.code_sha256 == sha256(code),
                    storage.id_codes.c.used_at.is_(None),
                )
                .values(used_at=now())
                .returning(storage.id_codes)
            )
            .mappings()
            .first()
        )
        account = storage.account_by_id(session, row["account_id"]) if row else None
    if (
        row is None
        or account is None
        or account["disabled_at"] is not None
        or row["expires_at"] <= now()
        or row["client_id"] != client["id"]
        or row["redirect_uri"] != form.get("redirect_uri")
        or not hmac.compare_digest(
            b64(hashlib.sha256(verifier.encode()).digest()), row["code_challenge"]
        )
    ):
        raise Refused("invalid_grant")
    issued = now()
    claims = {
        "ver": "2.0",
        "iss": issuer(public_url),
        "aud": str(client["id"]),
        "tid": str(TENANT),
        "oid": str(account["id"]),
        # Entra's sub is pairwise: the same person has a different sub in each application.
        "sub": b64(hashlib.sha256(f"{client['id']}:{account['id']}".encode()).digest()),
        "name": account["name"],
        "preferred_username": account["username"],
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int((issued + TOKEN_LIFETIME).timestamp()),
    }
    if row["nonce"]:
        claims["nonce"] = row["nonce"]
    if account["email"] and "email" in row["scope"].split():
        claims["email"] = account["email"]
    return {
        "token_type": "Bearer",
        "scope": row["scope"],
        "expires_in": int(TOKEN_LIFETIME.total_seconds()),
        "id_token": keys.sign(claims),
        # craft-id protects no APIs; the access token exists only because Entra always sends one.
        "access_token": b64(secrets.token_bytes(32)),
    }


def add_account(sessions, username: str, name: str, email: str | None, password: str) -> UUID:
    digest = PASSWORDS.hash(password)
    with unit_of_work(sessions) as session:
        identifier = session.execute(
            storage.id_accounts.insert()
            .values(username=username.lower(), name=name, email=email and email.lower())
            .returning(storage.id_accounts.c.id)
        ).scalar_one()
        session.execute(
            storage.id_credentials.insert().values(account_id=identifier, password_hash=digest)
        )
    return identifier


def set_password(sessions, username: str, password: str) -> bool:
    """Set or replace the password; an account seeded without one gets its first here."""
    digest = PASSWORDS.hash(password)
    with unit_of_work(sessions) as session:
        account = storage.account(session, username.lower())
        if account is None:
            return False
        session.execute(
            insert(storage.id_credentials)
            .values(account_id=account["id"], password_hash=digest)
            .on_conflict_do_update(
                index_elements=[storage.id_credentials.c.account_id],
                set_={"password_hash": digest, "updated_at": now()},
            )
        )
    return True


def disable_account(sessions, username: str) -> bool:
    with unit_of_work(sessions) as session:
        return (
            session.execute(
                storage.id_accounts.update()
                .where(storage.id_accounts.c.username == username.lower())
                .values(disabled_at=now())
                .returning(storage.id_accounts.c.id)
            ).first()
            is not None
        )


def add_client(sessions, name: str, redirect_uris: list[str]) -> tuple[UUID, str]:
    """Register an application; the secret is returned once and stored only as a hash."""
    secret = b64(secrets.token_bytes(32))
    with unit_of_work(sessions) as session:
        identifier = session.execute(
            storage.id_clients.insert()
            .values(name=name, secret_sha256=sha256(secret), redirect_uris=redirect_uris)
            .returning(storage.id_clients.c.id)
        ).scalar_one()
    return identifier, secret
