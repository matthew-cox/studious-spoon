import base64
import hashlib

import pytest

from shortener_admin.security import new_token, pkce_pair, safe_next_path, tokens_match


def test_new_tokens_are_long_and_unique():
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)  # 32 bytes, base64url


def test_pkce_pair_is_rfc7636_s256():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == expected


@pytest.mark.parametrize(
    ("expected", "supplied", "result"),
    [("abc", "abc", True), ("abc", "abd", False), ("abc", None, False), (None, "abc", False),
     ("", "", False), ("abc", "", False)],
)  # fmt: skip
def test_tokens_match(expected, supplied, result):
    assert tokens_match(expected, supplied) is result


@pytest.mark.parametrize("path", ["/", "/links", "/links/abc?status=blocked&page=2", "/links#x"])
def test_safe_next_accepts_local_paths(path):
    assert safe_next_path(path) == path


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "links", "//evil.example", "///evil.example", "/\\evil.example",
        "\\\\evil.example", "https://evil.example/", "http:/evil.example",
        "javascript:alert(1)", "/links\r\nSet-Cookie: x", "/\x00", " /links",
    ],
)  # fmt: skip
def test_safe_next_rejects_everything_else(raw):
    assert safe_next_path(raw) == "/"


def test_safe_next_custom_default():
    assert safe_next_path("//evil", default="/links") == "/links"
