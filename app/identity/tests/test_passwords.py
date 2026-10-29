"""An account without a password, as the frozen dataset's accounts are, gets one on reset."""

from app.identity import service, storage
from app.tests.support.identity import Provider


def test_reset_password_creates_a_missing_credential(provider: Provider) -> None:
    username, password, oid = provider.account()
    with provider.sessions.begin() as session:
        session.execute(
            storage.id_credentials.delete().where(storage.id_credentials.c.account_id == oid)
        )
    assert service.authenticate(provider.sessions, username, password) is None
    assert service.set_password(provider.sessions, username, "a-new-long-password")
    assert service.authenticate(provider.sessions, username, "a-new-long-password") is not None
    assert service.set_password(provider.sessions, username, "a-third-long-password")
    assert service.authenticate(provider.sessions, username, "a-new-long-password") is None
