"""craft-id's endpoints, on Entra's v2.0 paths under /id/{tenant}."""

import base64
import html
import logging
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.identity import service
from app.identity.service import TENANT, Keys, Refused, issuer

log = logging.getLogger(__name__)
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    "Referrer-Policy": "same-origin",  # no-referrer would send Origin: null on the form post
}
FIELDS = (
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "nonce",
    "code_challenge",
    "code_challenge_method",
    "response_mode",
)


def page(body: str, status: int = 200, also: str = "") -> HTMLResponse:
    """`also`: one more origin the form may post to, since browsers apply form-action to the
    redirect after the post (the registered redirect URI's)."""
    headers = dict(HEADERS)
    if also:
        headers["Content-Security-Policy"] += " " + also
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Sign in</title>"
        "<style>body{font:16px system-ui;max-width:22rem;margin:4rem auto}"
        "input,button{display:block;width:100%;margin:.4rem 0;padding:.5rem}</style>" + body,
        status_code=status,
        headers=headers,
    )


def form(request: dict, alert: str = "", status: int = 200) -> HTMLResponse:
    hidden = "".join(
        f'<input type=hidden name="{name}" value="{html.escape(request.get(name, ""))}">'
        for name in FIELDS
    )
    return page(
        "<h1>Sign in</h1>"
        + (f"<p role=alert>{alert}</p>" if alert else "")
        + "<form method=post><label>Username<input name=username autocomplete=username required "
        f'value="{html.escape(request.get("username", ""))}"></label>'
        "<label>Password<input name=password type=password autocomplete=current-password "
        "required></label>" + hidden + "<button>Sign in</button></form>",
        status,
        also=f"{target.scheme}://{target.netloc}"
        if (target := urlsplit(request.get("redirect_uri", ""))).netloc
        else "",
    )


def redirect(uri: str, values: dict) -> RedirectResponse:
    separator = "&" if urlsplit(uri).query else "?"
    return RedirectResponse(uri + separator + urlencode(values), 302, headers=HEADERS)


def create_identity_app(settings, sessions, keys: Keys) -> FastAPI:
    """The fake Entra. `sessions` logs in as craft_id; tests pass their own."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    base = f"/id/{TENANT}"
    authority = issuer(settings.id_public_url)
    public = settings.id_public_url + base

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    @app.get(base + "/v2.0/.well-known/openid-configuration")
    def discovery() -> dict:
        return {
            "issuer": authority,
            "authorization_endpoint": public + "/oauth2/v2.0/authorize",
            "token_endpoint": public + "/oauth2/v2.0/token",
            "jwks_uri": public + "/discovery/v2.0/keys",
            "end_session_endpoint": public + "/oauth2/v2.0/logout",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "subject_types_supported": ["pairwise"],
            "scopes_supported": sorted(service.SCOPES),
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
            "claims_supported": [
                "ver",
                "iss",
                "aud",
                "tid",
                "oid",
                "sub",
                "name",
                "preferred_username",
                "email",
                "nonce",
                "iat",
                "nbf",
                "exp",
            ],  # fmt: skip
        }

    @app.get(base + "/discovery/v2.0/keys")
    def jwks() -> dict:
        return {"keys": [keys.jwk]}

    async def checked(request: dict) -> Response | None:
        """An error page before the redirect URI is trusted, an error redirect after."""
        try:
            await run_in_threadpool(
                service.check_client,
                sessions,
                request.get("client_id", ""),
                request.get("redirect_uri", ""),
            )
        except Refused:
            return page("<h1>Sign-in request not valid</h1>", 400)
        problem = None
        if request.get("response_type", "code") != "code":
            problem = "unsupported_response_type"
        elif (
            "openid" not in request.get("scope", "").split()
            or not set(request.get("scope", "").split()) <= service.SCOPES
        ):
            problem = "invalid_scope"
        elif request.get("code_challenge_method") != "S256" or not request.get("code_challenge"):
            problem = "invalid_request"  # PKCE is required, as for Entra's confidential web apps
        elif request.get("response_mode", "query") != "query":
            problem = "invalid_request"
        if problem:
            values = {"error": problem}
            if request.get("state"):
                values["state"] = request["state"]
            return redirect(request["redirect_uri"], values)
        return None

    @app.get(base + "/oauth2/v2.0/authorize")
    async def authorize(request: Request) -> Response:
        values = dict(request.query_params)
        return await checked(values) or form(values)

    @app.post(base + "/oauth2/v2.0/authorize")
    async def sign_in(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin != settings.id_public_url:
            return page("<h1>Sign-in request not valid</h1>", 400)
        values = {key: str(value) for key, value in (await request.form()).items()}
        refusal = await checked(values)
        if refusal is not None:
            return refusal
        password = values.pop("password", "")
        try:
            account = await run_in_threadpool(
                service.authenticate, sessions, values.get("username", ""), password
            )
        except Refused:  # temporarily_unavailable
            log.info("identity.sign_in_busy")
            return form(values, "Too many sign-in attempts. Wait a minute and try again.", 429)
        if account is None:
            log.info("identity.sign_in_refused")
            return form(values, "That username and password did not match.", 401)
        client = await run_in_threadpool(
            service.check_client, sessions, values["client_id"], values["redirect_uri"]
        )
        code = await run_in_threadpool(service.issue_code, sessions, client, account, values)
        log.info("identity.code_issued", extra={"client": str(client["id"])})
        result = {"code": code}
        if values.get("state"):
            result["state"] = values["state"]
        return redirect(values["redirect_uri"], result)

    @app.post(base + "/oauth2/v2.0/token")
    async def token(request: Request) -> Response:
        values = {key: str(value) for key, value in (await request.form()).items()}
        basic = None
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, secret = base64.b64decode(header[6:]).decode().partition(":")
                basic = (user, secret)
            except ValueError:
                basic = ("", "")
        try:
            result = await run_in_threadpool(
                service.redeem, sessions, keys, settings.id_public_url, values, basic
            )
        except Refused as refused:
            log.info("identity.token_refused", extra={"error": refused.error})
            status = 401 if refused.error == "invalid_client" else 400
            return JSONResponse({"error": refused.error}, status, headers=HEADERS)
        return JSONResponse(result, headers=HEADERS)

    @app.get(base + "/oauth2/v2.0/logout")
    async def logout(request: Request) -> Response:
        # craft-id keeps no sign-in session of its own, so there is nothing to end here.
        target = request.query_params.get("post_logout_redirect_uri", "")
        client_id = request.query_params.get("client_id", "")
        if target and client_id:
            try:
                client = await run_in_threadpool(service.check_client, sessions, client_id, target)
            except Refused:
                client = None
            if client is not None:
                return RedirectResponse(target, 302, headers=HEADERS)
        return page("<h1>You are signed out</h1>")

    return app
