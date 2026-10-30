"""The OpenID Connect client: Entra's authorization code flow with PKCE.

It speaks plain OIDC, so pointing the three CRAFT_OIDC_* settings at Entra instead of craft-id is
the whole switch. (MSAL refuses http authorities, which the laptop's craft-id uses.)
"""

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt

from app.infrastructure.errors import ApiError

SCOPE = "openid profile email"
LEEWAY = 60  # seconds of clock skew allowed on iat, nbf and exp
http = httpx.Client(follow_redirects=True)  # tests swap in the in-process provider
_metadata: dict[str, tuple[float, dict]] = {}
_keys: dict[str, tuple[float, jwt.PyJWKSet]] = {}


class SignInFailed(ApiError):
    status, code, message = 401, "sign_in_failed", "Sign-in did not complete. Try again."


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _get(url: str) -> dict:
    try:
        response = http.get(url, timeout=5)
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        raise ApiError("The identity provider is not reachable.") from None
    if not isinstance(document, dict):
        raise ApiError("The identity provider is misconfigured.")
    return document


def metadata(authority: str) -> dict:
    """The provider's discovery document, kept for an hour; its issuer must be the authority."""
    cached = _metadata.get(authority)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    document = _get(authority + "/.well-known/openid-configuration")
    endpoints = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    if document.get("issuer") != authority or not all(
        isinstance(document.get(name), str) for name in endpoints
    ):
        raise ApiError("The identity provider is misconfigured.")
    _metadata[authority] = (time.monotonic() + 3600, document)
    return document


def signing_key(authority: str, kid: str) -> jwt.PyJWK:
    """The key for `kid`; an unknown kid refetches the set once (Entra rotates keys)."""
    for attempt in range(2):
        cached = _keys.get(authority)
        if attempt or not cached or cached[0] <= time.monotonic():
            keys = jwt.PyJWKSet.from_dict(_get(metadata(authority)["jwks_uri"]))
            cached = _keys[authority] = (time.monotonic() + 3600, keys)
        for key in cached[1].keys:
            if key.key_id == kid:
                return key
    raise SignInFailed()


def begin(settings) -> tuple[str, dict]:
    """The authorize URL and what the callback needs to check it (state, nonce, PKCE verifier)."""
    flow = {
        "state": b64(secrets.token_bytes(16)),
        "nonce": b64(secrets.token_bytes(16)),
        "verifier": b64(secrets.token_bytes(32)),
    }
    query = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "response_mode": "query",
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": SCOPE,
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": b64(hashlib.sha256(flow["verifier"].encode()).digest()),
        "code_challenge_method": "S256",
    }
    endpoint = metadata(settings.oidc_authority)["authorization_endpoint"]
    return endpoint + "?" + urlencode(query), flow


def finish(settings, flow: dict, state: str | None, code: str | None) -> dict:
    """Redeem the code and return the ID token's claims after every check Entra's docs require."""
    if not state or not code or not hmac.compare_digest(state, flow["state"]):
        raise SignInFailed()
    authority = settings.oidc_authority
    try:
        response = http.post(
            metadata(authority)["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": flow["verifier"],
                "scope": SCOPE,
            },
            timeout=10,
        )
        token = response.json().get("id_token") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        token = None
    if not isinstance(token, str):
        raise SignInFailed()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise SignInFailed()
        claims = jwt.decode(
            token,
            signing_key(authority, header.get("kid", "")),
            algorithms=["RS256"],
            audience=settings.oidc_client_id,
            issuer=authority,
            leeway=LEEWAY,
            options={"require": ["iss", "aud", "exp", "iat", "nbf", "tid", "oid", "nonce"]},
        )
    except jwt.PyJWTError:
        raise SignInFailed() from None
    if not hmac.compare_digest(str(claims["nonce"]), flow["nonce"]):
        raise SignInFailed()
    try:
        claims["tid"], claims["oid"] = UUID(str(claims["tid"])), UUID(str(claims["oid"]))
    except ValueError:
        raise SignInFailed() from None
    if f"/{claims['tid']}/" not in authority + "/":  # Entra's issuer names its tenant
        raise SignInFailed()
    return claims


def logout_url(settings, after: str) -> str | None:
    endpoint = metadata(settings.oidc_authority).get("end_session_endpoint")
    if not endpoint:
        return None
    return (
        endpoint
        + "?"
        + urlencode({"post_logout_redirect_uri": after, "client_id": settings.oidc_client_id})
    )
