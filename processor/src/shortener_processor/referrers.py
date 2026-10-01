"""Referer header → referrer host for daily rollups (spec §4.2). Pure."""

from urllib.parse import urlsplit

DIRECT = "(direct)"
MAX_HOST_LENGTH = 255


def referrer_host(referrer: str | None) -> str:
    """Lower-cased host of the Referer, or "(direct)" if absent or unparseable."""
    if not referrer or not referrer.strip():
        return DIRECT
    try:
        host = urlsplit(referrer.strip()).hostname
    except ValueError:  # e.g. malformed IPv6 brackets
        return DIRECT
    host = (host or "").rstrip(".")
    return host[:MAX_HOST_LENGTH] if host else DIRECT
