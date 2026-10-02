import pytest

from shortener_api.urls import (
    MAX_URL_LENGTH,
    InvalidTargetUrl,
    short_code_from,
    validate_target_url,
)

OWN = "http://sho.rt"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/path?q=1#frag",
        "https://sub.example.co.uk:8443/x",
        "http://sho.rt:8080/other-port-is-a-different-site",
        "https://例え.jp/",
    ],
)
def test_accepts(url):
    assert validate_target_url(url, OWN) == url


def test_strips_surrounding_whitespace():
    assert validate_target_url("  https://example.com/x \n", OWN) == "https://example.com/x"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("javascript:alert(1)", "scheme"),
        ("data:text/html,<script>", "scheme"),
        ("ftp://example.com/file", "scheme"),
        ("//example.com/no-scheme", "scheme"),
        ("example.com", "scheme"),
        ("https://", "host"),
        ("https://user:pass@example.com/", "credentials"),
        ("https://user@example.com/", "credentials"),
        ("https://exa mple.com/", "whitespace"),
        ("https://example.com/a\nb", "whitespace"),
        ("https://example.com/\x00", "whitespace"),
        ("http://sho.rt/abc", "this shortener"),
        ("http://SHO.RT/abc", "this shortener"),
        ("http://sho.rt:80/abc", "this shortener"),
        ("", "empty"),
    ],
)
def test_rejects(url, message):
    with pytest.raises(InvalidTargetUrl, match=message):
        validate_target_url(url, OWN)


def test_length_limit_is_inclusive():
    base = "https://example.com/"
    ok = base + "a" * (MAX_URL_LENGTH - len(base))
    assert validate_target_url(ok, OWN) == ok
    with pytest.raises(InvalidTargetUrl, match="2048"):
        validate_target_url(ok + "a", OWN)


def test_own_host_with_explicit_https_port():
    with pytest.raises(InvalidTargetUrl, match="this shortener"):
        validate_target_url("https://short.example:443/x", "https://short.example")


def test_invalid_port_is_rejected():
    with pytest.raises(InvalidTargetUrl, match="port"):
        validate_target_url("https://example.com:99999/", OWN)


@pytest.mark.parametrize("url", ["http://[", "https://[::1", "http://[not-ip]/"])
def test_malformed_url_is_rejected_not_raised_as_value_error(url):
    with pytest.raises(InvalidTargetUrl, match="malformed"):
        validate_target_url(url, OWN)


@pytest.mark.parametrize(
    "url",
    [
        "http://sho.rt./x",
        "http://sho.rt%2e/x",
        "http://SHO.RT%2E:80/x",
        "http://sho.rt../x",
    ],
)
def test_own_host_in_trailing_dot_or_escaped_form_is_rejected(url):
    with pytest.raises(InvalidTargetUrl, match="this shortener"):
        validate_target_url(url, OWN)


# A phishing report quotes the full short URL; searching for it should find the link.
@pytest.mark.parametrize(
    "pasted",
    [
        "http://sho.rt/AbC1234",
        "  http://sho.rt/AbC1234  ",
        "https://sho.rt/AbC1234",  # reports often "upgrade" the scheme
        "HTTP://SHO.RT/AbC1234",
        "http://sho.rt:80/AbC1234",
        "sho.rt/AbC1234",
        "http://sho.rt/AbC1234/",
        "http://sho.rt/AbC1234?utm_source=mail#top",
    ],
)
def test_short_code_from_a_pasted_short_url(pasted):
    assert short_code_from(pasted, OWN) == "AbC1234"


@pytest.mark.parametrize(
    "text",
    [
        "AbC1234",  # a bare code is already a fine search term
        "example.com",
        "https://example.com/AbC1234",
        "http://sho.rt",
        "http://sho.rt/",
        "http://sho.rt/a/b",
        "http://sho.rt/not-a-code",
        "http://sho.rt:8080/AbC1234",
        "http://sho.rt:bad/AbC1234",
        "http://[::1/AbC1234",
        "",
    ],
)
def test_short_code_from_anything_else_is_none(text):
    assert short_code_from(text, OWN) is None


def test_short_code_from_respects_host_port_and_base_path():
    assert short_code_from("localhost:8000/AbC1234", "http://localhost:8000") == "AbC1234"
    assert short_code_from("localhost:8000/AbC1234", "http://localhost:8001") is None
    assert short_code_from("https://go.example/s/AbC1234", "https://go.example/s/") == "AbC1234"
    assert short_code_from("https://go.example/AbC1234", "https://go.example/s") is None
