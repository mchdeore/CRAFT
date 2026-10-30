"""Sign-in posts: one password check at a time, and five tries a minute for each username."""

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from uuid import uuid4

from app.identity import service
from app.tests.support.identity import REDIRECT, Provider


def post(provider: Provider, username: str, password: str = "not-the-password"):
    form = {
        "client_id": provider.client_id,
        "redirect_uri": REDIRECT,
        "scope": "openid",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "username": username,
        "password": password,
    }
    return provider.client.post(
        provider.authority.removesuffix("/v2.0") + "/oauth2/v2.0/authorize", data=form
    )


def test_one_password_check_at_a_time(provider: Provider, monkeypatch) -> None:
    verify, lock, release = service.PASSWORDS.verify, threading.Lock(), threading.Event()
    running = most = 0

    def counting(digest: str, password: str) -> bool:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        release.wait(10)  # held until the other posts have their answers
        with lock:
            running -= 1
        return verify(digest, password)

    monkeypatch.setattr(service, "PASSWORDS", SimpleNamespace(verify=counting))
    monkeypatch.setattr(service, "VERIFY_WAIT", 0.05)
    answers = []
    with ThreadPoolExecutor(5) as pool:
        posts = [pool.submit(post, provider, f"user-{uuid4().hex[:8]}") for _ in range(5)]
        for done in as_completed(posts, timeout=30):
            answers.append(done.result())
            if len(answers) == 4:
                release.set()

    assert most == 1
    assert [answer.status_code for answer in answers] == [429, 429, 429, 429, 401]
    assert all("try again" in answer.text for answer in answers[:4])


def test_five_tries_a_minute_for_each_username(provider: Provider, monkeypatch) -> None:
    username, password, _ = provider.account()
    assert [post(provider, username).status_code for _ in range(5)] == [401] * 5
    assert post(provider, username, password).status_code == 429
    assert post(provider, f"user-{uuid4().hex[:8]}").status_code == 401  # others may still try

    monkeypatch.setattr(service, "ATTEMPTS", deque((at - 60, key) for at, key in service.ATTEMPTS))
    assert post(provider, username, password).status_code == 302  # a minute later
