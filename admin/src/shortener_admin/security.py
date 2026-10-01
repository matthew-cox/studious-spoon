"""Security primitives (pure, stdlib only): tokens, PKCE, constant-time compare, safe redirects."""

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)  # 86 chars of [A-Za-z0-9_-]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def tokens_match(expected: str | None, supplied: str | None) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode(), supplied.encode())


def safe_next_path(raw: str | None, default: str = "/") -> str:
    """Only same-origin absolute paths survive; everything else becomes `default`."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return default
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return default
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return default
    return raw
