"""Target-URL validation (spec §4.4) and short-URL recognition. Pure: no framework imports."""

import re
from urllib.parse import SplitResult, unquote, urlsplit

MAX_URL_LENGTH = 2048
_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_CODE = re.compile(r"[A-Za-z0-9]{1,32}")


class InvalidTargetUrl(ValueError):
    pass


def _split(url: str) -> SplitResult:
    try:
        return urlsplit(url)
    except ValueError as exc:
        raise InvalidTargetUrl("URL is malformed") from exc


def _authority(parts: SplitResult) -> tuple[str, int]:
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidTargetUrl("URL has an invalid port") from exc
    host = unquote(parts.hostname or "").lower().rstrip(".")
    return host, port or _DEFAULT_PORTS[parts.scheme.lower()]


def validate_target_url(raw: str, own_base_url: str) -> str:
    """Return the cleaned URL, or raise InvalidTargetUrl explaining why it can't be shortened."""
    url = raw.strip()
    if not url:
        raise InvalidTargetUrl("URL is empty")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidTargetUrl(f"URL is longer than {MAX_URL_LENGTH} characters")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise InvalidTargetUrl("URL contains whitespace or control characters")
    parts = _split(url)
    if parts.scheme.lower() not in _SCHEMES:
        raise InvalidTargetUrl("URL scheme must be http or https")
    if not parts.hostname:
        raise InvalidTargetUrl("URL must include a host")
    if parts.username is not None or parts.password is not None:
        raise InvalidTargetUrl("URL must not contain credentials")
    if _authority(parts) == _authority(_split(own_base_url)):
        raise InvalidTargetUrl("URL must not point at this shortener")
    return url


def _site(parts: SplitResult, fallback_scheme: str) -> tuple[str, int | None]:
    """(host, port) with the scheme's default port written as None, so http/https both match."""
    port = parts.port  # may raise ValueError; callers treat that as "not ours"
    if port == _DEFAULT_PORTS.get(parts.scheme.lower() or fallback_scheme):
        port = None
    return (parts.hostname or "").lower().rstrip("."), port


def short_code_from(text: str, own_base_url: str) -> str | None:
    """The code in a pasted short URL of this shortener ("https://sho.rt/AbC1234?x" -> "AbC1234").

    Scheme, query, fragment and a trailing slash are ignored; anything else returns None.
    """
    pasted = text.strip()
    if "://" not in pasted:
        pasted = f"//{pasted}"  # "sho.rt/AbC1234": parse the host as a host, not a path
    base = urlsplit(own_base_url)
    try:
        parts = urlsplit(pasted)
        if not parts.hostname or _site(parts, base.scheme) != _site(base, base.scheme):
            return None
    except ValueError:
        return None
    prefix = base.path.rstrip("/") + "/"
    if not parts.path.startswith(prefix):
        return None
    code = parts.path[len(prefix) :].rstrip("/")
    return code if _CODE.fullmatch(code) else None
