"""Validate and persist the complete user request for isolated semantic stages."""

from value_contracts import require_text


MAX_REQUEST_CHARS = 32768
MAX_LIST_ITEMS = 100
MAX_ITEM_CHARS = 512
FIELDS = {"schema_version", "request", "scope", "exclusions", "emphasis"}


def text_list(value, label):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"{label} must be an array of at most {MAX_LIST_ITEMS} strings")
    normalized = []
    for index, item in enumerate(value):
        text = require_text(item, f"{label}[{index}]")
        if len(text) > MAX_ITEM_CHARS:
            raise ValueError(
                f"{label}[{index}] exceeds {MAX_ITEM_CHARS} characters"
            )
        normalized.append(text)
    return list(dict.fromkeys(normalized))


def normalize(value):
    if not isinstance(value, dict):
        raise ValueError("request context must be an object")
    unknown = sorted(set(value) - FIELDS)
    if unknown:
        raise ValueError(
            "request context contains unknown fields: " + ", ".join(unknown)
        )
    if value.get("schema_version") != 1:
        raise ValueError("request context schema_version must be 1")
    request = require_text(value.get("request"), "request context.request")
    if len(request) > MAX_REQUEST_CHARS:
        raise ValueError(
            f"request context.request exceeds {MAX_REQUEST_CHARS} characters"
        )
    return {
        "schema_version": 1,
        "request": request,
        "scope": text_list(value.get("scope"), "request context.scope"),
        "exclusions": text_list(
            value.get("exclusions"),
            "request context.exclusions",
        ),
        "emphasis": text_list(value.get("emphasis"), "request context.emphasis"),
    }


def fallback(domains):
    return {
        "schema_version": 1,
        "request": "生成当前主体在已解析周期内的个人工作总结。",
        "scope": [f"数据域：{domain}" for domain in domains],
        "exclusions": [],
        "emphasis": [],
    }
