"""MSAL's confidential client runs Entra's code flow against craft-id unchanged.

MSAL accepts only https authorities, so craft-app uses its own OIDC client (app/accounts/oidc.py);
this test keeps craft-id honest against Microsoft's own library.
"""

import hashlib
from urllib.parse import parse_qsl, urlsplit

import msal

from app.tests.support.identity import REDIRECT, Provider


def test_msal_code_flow_against_craft_id(provider: Provider) -> None:
    username, password, oid = provider.account(email="person@example.org")
    app = msal.ConfidentialClientApplication(
        provider.client_id,
        client_credential=provider.secret,
        oidc_authority=provider.authority,
        http_client=provider.http,
    )
    flow = app.initiate_auth_code_flow(["email"], redirect_uri=REDIRECT)
    assert provider.client.get(flow["auth_uri"]).status_code == 200
    form = dict(parse_qsl(urlsplit(flow["auth_uri"]).query))
    answer = provider.client.post(
        flow["auth_uri"].split("?")[0], data=form | {"username": username, "password": password}
    )
    assert answer.status_code == 302
    location = answer.headers["location"]
    assert location.startswith(REDIRECT + "?")

    result = app.acquire_token_by_auth_code_flow(flow, dict(parse_qsl(urlsplit(location).query)))

    assert "error" not in result, result.get("error")
    claims = result["id_token_claims"]
    assert claims["oid"] == str(oid)
    assert claims["iss"] == provider.authority
    assert claims["aud"] == provider.client_id
    assert claims["email"] == "person@example.org"
    assert claims["nonce"] == hashlib.sha256(flow["nonce"].encode()).hexdigest()  # MSAL hashes it
