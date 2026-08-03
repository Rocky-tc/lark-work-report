"""Shared validation for report evidence source references."""

import re
from urllib.parse import urlsplit


HTTP_TARGET = r"https?://[^\s()\[\]<>\\]+"
STABLE_TARGET = (
    r"source://[A-Za-z0-9][A-Za-z0-9._~-]*"
    r"(?:/[A-Za-z0-9._~:@%+-]+)+"
)
SOURCE_TARGET = rf"(?:{HTTP_TARGET}|{STABLE_TARGET})"
SOURCE_REF = re.compile(rf"^{SOURCE_TARGET}$")


def is_valid_source_ref(value):
    if not isinstance(value, str) or not SOURCE_REF.fullmatch(value):
        return False
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    if parsed.scheme == "source":
        return bool(parsed.netloc and parsed.path.strip("/"))
    return False
