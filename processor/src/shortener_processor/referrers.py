"""Referer header → referrer host for daily rollups (spec §4.2). Pure."""

from urllib.parse import urlsplit

DIRECT = "(direct)"
_DEL = 0x7F
MAX_HOST_LENGTH = 255


def has_control_char(text: str) -> bool:
    """True if `text` holds a C0 control character or DEL (Postgres rejects NUL in text)."""
    return any(ord(ch) < 0x20 or ord(ch) == _DEL for ch in text)


def referrer_host(referrer: str | None) -> str:
    """Lower-cased host of the Referer, or "(direct)" if absent or unparseable."""
    if not referrer or not referrer.strip():
        return DIRECT
    try:
        host = urlsplit(referrer.strip()).hostname
    except ValueError:  # e.g. malformed IPv6 brackets
        return DIRECT
    host = (host or "").rstrip(".")
    if not host or has_control_char(host):
        return DIRECT
    return host[:MAX_HOST_LENGTH]
