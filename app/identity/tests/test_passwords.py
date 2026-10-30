"""Passwords: an account without one, as the frozen dataset's accounts are, gets one on reset,
and none is shorter than 12 characters."""

from uuid import uuid4

import pytest

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
    assert service.authenticate(provider.sessions, username, "a-third-long-password") is not None


def test_short_passwords_are_refused_and_write_nothing(provider: Provider) -> None:
    username, password, _ = provider.account()
    newcomer = f"user-{uuid4().hex[:8]}"
    for short in ["", "x" * 11]:
        with pytest.raises(ValueError):
            service.add_account(provider.sessions, newcomer, "Newcomer", None, short)
        with pytest.raises(ValueError):
            service.set_password(provider.sessions, username, short)
    with provider.sessions() as session:
        assert storage.account(session, newcomer) is None
    assert service.authenticate(provider.sessions, username, password) is not None
    assert service.set_password(provider.sessions, username, "x" * 12)
