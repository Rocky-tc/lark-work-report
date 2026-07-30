"""Shared machine contracts for collection and report scripts."""


SOURCE_PRIORITY = (
    "report_cache",
    "tasks",
    "calendar",
    "vc",
    "minutes",
    "mentions",
    "comments",
    "docs",
    "wiki",
    "base",
    "im",
    "mail",
    "okr",
    "approval",
    "code_activity",
    "ai_sessions",
)
SOURCE_TYPES = frozenset(SOURCE_PRIORITY)
SOURCE_RANK = {
    source_type: index for index, source_type in enumerate(SOURCE_PRIORITY)
}

SIGNAL_KINDS = {"outcome", "action", "decision", "risk", "event"}
ACTION_KINDS = {
    "reply",
    "prepare",
    "review",
    "deliver",
    "follow_up",
    "attend",
}
ASSIGNEE_RELATIONS = {"self", "shared", "unknown"}
STATUS_BASES = {"explicit", "stated_plan", "inferred"}
EVIDENCE_CONFIDENCE = {"high", "medium", "low"}

PROFILE_SECTIONS = {
    "daily": (
        "今日摘要",
        "工作进展与结果",
        "风险与需协助事项",
        "明日重点",
        "待复核",
        "来源与覆盖",
    ),
    "weekly": (
        "本周摘要",
        "工作流进展与结果",
        "风险与需协助事项",
        "下周重点",
        "待复核",
        "来源与覆盖",
    ),
    "monthly": (
        "月度摘要",
        "目标与工作流进展",
        "风险与依赖",
        "下月重点",
        "待复核",
        "来源与覆盖",
    ),
}

PROFILE_PERIOD_LABELS = {
    "daily": "个人日报",
    "weekly": "个人周报",
    "monthly": "个人月报",
}

STATUS_LABELS = {
    "completed": "已完成",
    "in_progress": "进行中",
    "blocked": "阻塞",
    "planned": "计划中",
}
