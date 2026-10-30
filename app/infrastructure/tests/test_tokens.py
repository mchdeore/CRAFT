import base64

import pytest

from app.infrastructure.tokens import new_token, read_token


def test_a_new_token_reads_back_to_its_bytes() -> None:
    raw, text = new_token()
    assert len(raw) == 32 and "=" not in text
    assert read_token(text) == raw
    assert new_token()[0] != raw


ZERO = "A" * 43  # 32 zero bytes as unpadded base64url


@pytest.mark.parametrize(
    "text",
    [
        "",
        ZERO + "=",  # padded
        ZERO[:-1] + "B",  # the same bytes, with nonzero unused bits
        base64.b64encode(b"\xfb" * 32).decode().rstrip("="),  # standard alphabet
        base64.urlsafe_b64encode(bytes(31)).decode().rstrip("="),  # too short
        base64.urlsafe_b64encode(bytes(33)).decode().rstrip("="),  # too long
        ZERO[:-1] + "!",
        ZERO[:-1] + "é",
    ],
)
def test_anything_else_reads_as_none(text: str) -> None:
    assert read_token(ZERO) == bytes(32)
    assert read_token(text) is None
