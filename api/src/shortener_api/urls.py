"""Target-URL validation (spec §4.4). Pure: no framework imports."""

from urllib.parse import SplitResult, urlsplit

MAX_URL_LENGTH = 2048
_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidTargetUrl(ValueError):
    pass


def _authority(parts: SplitResult) -> tuple[str, int]:
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidTargetUrl("URL has an invalid port") from exc
    host = (parts.hostname or "").lower()
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
    parts = urlsplit(url)
    if parts.scheme.lower() not in _SCHEMES:
        raise InvalidTargetUrl("URL scheme must be http or https")
    if not parts.hostname:
        raise InvalidTargetUrl("URL must include a host")
    if parts.username is not None or parts.password is not None:
        raise InvalidTargetUrl("URL must not contain credentials")
    if _authority(parts) == _authority(urlsplit(own_base_url)):
        raise InvalidTargetUrl("URL must not point at this shortener")
    return url
