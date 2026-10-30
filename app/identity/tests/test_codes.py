"""craft-id keeps only the codes that can still be redeemed."""

from sqlalchemy import select

from app.identity import service, storage
from app.infrastructure.access import now
from app.tests.support.identity import REDIRECT, Provider


def test_issuing_a_code_removes_expired_and_used_codes(provider: Provider) -> None:
    _, _, oid = provider.account()
    client = service.check_client(provider.sessions, provider.client_id, REDIRECT)
    request = {"redirect_uri": REDIRECT, "scope": "openid", "code_challenge": "x" * 43}
    used, expired, kept = (
        service.issue_code(provider.sessions, client, {"id": oid}, request) for _ in range(3)
    )
    codes = storage.id_codes.c
    with provider.sessions.begin() as session:
        for code, change in ((used, {"used_at": now()}), (expired, {"expires_at": now()})):
            session.execute(
                storage.id_codes.update()
                .where(codes.code_sha256 == service.sha256(code))
                .values(change)
            )

    fresh = service.issue_code(provider.sessions, client, {"id": oid}, request)

    with provider.sessions() as session:
        left = session.execute(select(codes.code_sha256).where(codes.account_id == oid)).scalars()
        assert set(left) == {service.sha256(kept), service.sha256(fresh)}
