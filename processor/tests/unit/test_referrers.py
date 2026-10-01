import pytest

from shortener_processor.referrers import DIRECT, MAX_HOST_LENGTH, referrer_host


@pytest.mark.parametrize(
    ("referrer", "expected"),
    [
        ("https://news.ycombinator.com/item?id=1", "news.ycombinator.com"),
        ("HTTPS://News.Example.COM/Path", "news.example.com"),
        ("http://example.com:8080/x", "example.com"),
        ("https://user:pass@example.com/", "example.com"),
        ("https://example.com./", "example.com"),
        ("android-app://com.google.android.gm/", "com.google.android.gm"),
        ("  https://padded.example/  ", "padded.example"),
    ],
)
def test_extracts_lowercased_host(referrer, expected):
    assert referrer_host(referrer) == expected


@pytest.mark.parametrize(
    "referrer",
    [None, "", "   ", "news.example/no-scheme", "/relative/path", "http://[", "https://"],
)
def test_unusable_referrers_are_direct(referrer):
    assert referrer_host(referrer) == DIRECT


def test_direct_marker_is_spec_value():
    assert DIRECT == "(direct)"


def test_very_long_hosts_are_truncated():
    host = "a" * 300 + ".example"
    assert referrer_host(f"https://{host}/") == host[:MAX_HOST_LENGTH]
