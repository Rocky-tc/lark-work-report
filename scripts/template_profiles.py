"""Validate and resolve portable personal report template profiles."""

import json
import re
from pathlib import Path

from contracts import PROFILE_SECTIONS


MAX_TEMPLATE_BYTES = 8 * 1024
TEMPLATE_ID = "default"
SEMANTIC_SLOTS = (
    "summary",
    "progress",
    "risks",
    "next",
    "uncertain",
    "coverage",
)
REPORT_PROFILES = tuple(PROFILE_SECTIONS)
ITEM_STYLES = {"bullet", "numbered", "paragraph"}
WORKSTREAM_LAYOUTS = {"inline", "subsection"}
TONE_REGISTERS = {"concise", "formal", "direct", "narrative"}
TONE_VOICES = {"neutral", "first_person"}
TONE_DENSITIES = {"compact", "standard"}
TITLE_PLACEHOLDERS = {"subject", "period_label", "period_range"}
FIELD_LABEL_DEFAULTS = {
    "impact": "影响",
    "decision": "决策",
    "progress": "进展",
    "assistance": "需协助",
    "purpose": "目标",
    "reason": "原因",
}
TEMPLATE_FIELDS = {
    "schema_version",
    "template_id",
    "source_fingerprint",
    "title_pattern",
    "sections",
    "workstream_layout",
    "field_labels",
    "tone",
}
SECTION_FIELDS = {"labels", "item_style"}
TONE_FIELDS = {"register", "voice", "density"}
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
MARKDOWN_CONTROL = re.compile(r"[#\[\]`<>\\]")
FORBIDDEN_INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"忽略(?:之前|以上).{0,8}指令|执行(?:以下)?(?:命令|指令)|"
    r"调用(?:工具|接口)|(?:bash|shell|python)\s+-?[cm]\b",
    re.IGNORECASE,
)


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def reject_unknown_fields(value, allowed, label):
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def safe_text(value, label, *, maximum):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = " ".join(value.split())
    if len(text) > maximum:
        raise ValueError(f"{label} must not exceed {maximum} characters")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a single line")
    if MARKDOWN_CONTROL.search(text):
        raise ValueError(f"{label} contains unsafe Markdown control characters")
    if FORBIDDEN_INSTRUCTION.search(text):
        raise ValueError(f"{label} contains executable or prompt-like instructions")
    return text


def validate_title_pattern(value):
    text = safe_text(value, "title_pattern", maximum=160)
    placeholders = set(PLACEHOLDER.findall(text))
    unknown = placeholders - TITLE_PLACEHOLDERS
    if unknown:
        raise ValueError(
            "title_pattern contains unsupported placeholders: "
            + ", ".join(sorted(unknown))
        )
    residue = PLACEHOLDER.sub("", text)
    if "{" in residue or "}" in residue:
        raise ValueError("title_pattern contains malformed placeholders")
    return text


def validate_sections(value):
    sections = require_object(value, "sections")
    reject_unknown_fields(sections, set(SEMANTIC_SLOTS), "sections")
    missing = [slot for slot in SEMANTIC_SLOTS if slot not in sections]
    if missing:
        raise ValueError(f"sections is missing semantic slots: {', '.join(missing)}")

    normalized = {}
    labels_by_profile = {profile: [] for profile in REPORT_PROFILES}
    for slot in SEMANTIC_SLOTS:
        section = require_object(sections[slot], f"sections.{slot}")
        reject_unknown_fields(section, SECTION_FIELDS, f"sections.{slot}")
        labels = require_object(section.get("labels"), f"sections.{slot}.labels")
        reject_unknown_fields(labels, set(REPORT_PROFILES), f"sections.{slot}.labels")
        missing_profiles = [
            profile for profile in REPORT_PROFILES if profile not in labels
        ]
        if missing_profiles:
            raise ValueError(
                f"sections.{slot}.labels is missing profiles: "
                + ", ".join(missing_profiles)
            )
        normalized_labels = {}
        for profile in REPORT_PROFILES:
            label = safe_text(
                labels[profile],
                f"sections.{slot}.labels.{profile}",
                maximum=48,
            )
            normalized_labels[profile] = label
            labels_by_profile[profile].append(label)

        item_style = section.get("item_style")
        if item_style not in ITEM_STYLES:
            raise ValueError(
                f"sections.{slot}.item_style must be one of "
                + ", ".join(sorted(ITEM_STYLES))
            )
        if slot == "coverage" and item_style != "bullet":
            raise ValueError("sections.coverage.item_style must be bullet")
        normalized[slot] = {
            "labels": normalized_labels,
            "item_style": item_style,
        }

    for profile, labels in labels_by_profile.items():
        if len(set(labels)) != len(labels):
            raise ValueError(f"section labels must be unique for profile {profile}")
    return normalized


def validate_field_labels(value):
    labels = require_object(value, "field_labels")
    reject_unknown_fields(labels, set(FIELD_LABEL_DEFAULTS), "field_labels")
    missing = [field for field in FIELD_LABEL_DEFAULTS if field not in labels]
    if missing:
        raise ValueError(f"field_labels is missing fields: {', '.join(missing)}")
    return {
        field: safe_text(labels[field], f"field_labels.{field}", maximum=16)
        for field in FIELD_LABEL_DEFAULTS
    }


def validate_tone(value):
    tone = require_object(value, "tone")
    reject_unknown_fields(tone, TONE_FIELDS, "tone")
    register = tone.get("register")
    voice = tone.get("voice")
    density = tone.get("density")
    if register not in TONE_REGISTERS:
        raise ValueError("tone.register has an unsupported value")
    if voice not in TONE_VOICES:
        raise ValueError("tone.voice has an unsupported value")
    if density not in TONE_DENSITIES:
        raise ValueError("tone.density has an unsupported value")
    return {"register": register, "voice": voice, "density": density}


def validate(payload, *, encoded_size=None):
    profile = require_object(payload, "template profile")
    reject_unknown_fields(profile, TEMPLATE_FIELDS, "template profile")
    if encoded_size is None:
        encoded_size = len(
            json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
    if encoded_size > MAX_TEMPLATE_BYTES:
        raise ValueError(f"template profile must not exceed {MAX_TEMPLATE_BYTES} bytes")
    if profile.get("schema_version") != 1:
        raise ValueError("template profile schema_version must be 1")
    if profile.get("template_id") != TEMPLATE_ID:
        raise ValueError(f"template_id must be {TEMPLATE_ID}")
    fingerprint = profile.get("source_fingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("source_fingerprint must be sha256:<64 lowercase hex>")
    workstream_layout = profile.get("workstream_layout")
    if workstream_layout not in WORKSTREAM_LAYOUTS:
        raise ValueError(
            "workstream_layout must be one of "
            + ", ".join(sorted(WORKSTREAM_LAYOUTS))
        )
    return {
        "schema_version": 1,
        "template_id": TEMPLATE_ID,
        "source_fingerprint": fingerprint,
        "title_pattern": validate_title_pattern(profile.get("title_pattern")),
        "sections": validate_sections(profile.get("sections")),
        "workstream_layout": workstream_layout,
        "field_labels": validate_field_labels(profile.get("field_labels")),
        "tone": validate_tone(profile.get("tone")),
    }


def load(path):
    source = Path(path)
    raw = source.read_bytes()
    if len(raw) > MAX_TEMPLATE_BYTES:
        raise ValueError(f"template profile must not exceed {MAX_TEMPLATE_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("template profile is not valid UTF-8 JSON") from exc
    return validate(payload, encoded_size=len(raw))


def section_settings(template, profile):
    if profile not in PROFILE_SECTIONS:
        raise ValueError(f"unknown report profile: {profile}")
    if template is None:
        return [
            {
                "slot": slot,
                "label": PROFILE_SECTIONS[profile][index],
                "item_style": "bullet",
            }
            for index, slot in enumerate(SEMANTIC_SLOTS)
        ]
    return [
        {
            "slot": slot,
            "label": template["sections"][slot]["labels"][profile],
            "item_style": template["sections"][slot]["item_style"],
        }
        for slot in SEMANTIC_SLOTS
    ]


def field_labels(template):
    return (
        dict(FIELD_LABEL_DEFAULTS)
        if template is None
        else dict(template["field_labels"])
    )


def workstream_layout(template):
    return "inline" if template is None else template["workstream_layout"]


def render_title(template, fallback, context):
    if template is None or not isinstance(context, dict):
        return fallback
    values = {}
    for name in TITLE_PLACEHOLDERS:
        value = context.get(name)
        if not isinstance(value, str) or not value.strip():
            return fallback
        values[name] = " ".join(value.split())
    return template["title_pattern"].format(**values)
