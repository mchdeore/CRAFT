"""The sign-in form works in a real browser, which applies these headers."""

from urllib.parse import parse_qsl, urlsplit

from app.tests.support.identity import REDIRECT, Provider


def test_form_may_post_through_to_the_registered_redirect(provider: Provider) -> None:
    query = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "scope": "openid",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
    }
    response = provider.client.get(
        provider.authority.removesuffix("/v2.0") + "/oauth2/v2.0/authorize", params=query
    )
    target = urlsplit(REDIRECT)
    # Browsers apply form-action to the redirect after the post, and send Origin: null under
    # no-referrer, which the form's Origin check refuses.
    assert (
        f"form-action 'self' {target.scheme}://{target.netloc}"
        in response.headers["content-security-policy"]
    )
    assert response.headers["referrer-policy"] == "same-origin"
    assert dict(parse_qsl(urlsplit(str(response.url)).query))["client_id"] == provider.client_id
