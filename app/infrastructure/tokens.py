"""Bearer secrets: 32 random bytes, written as unpadded base64url. Only their SHA-256 digest is
stored."""

import base64
import secrets


def new_token() -> tuple[bytes, str]:
    """A fresh secret and the text it is handed out as."""
    raw = secrets.token_bytes(32)
    return raw, base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_token(text: str) -> bytes | None:
    """The 32 bytes behind a token written exactly as `new_token` writes it; None for anything
    else, including padding, standard base64 characters and any other length."""
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != text:
        return None
    return raw
