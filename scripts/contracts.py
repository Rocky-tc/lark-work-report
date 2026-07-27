"""Shared machine contracts for collection and report scripts."""


SOURCE_TYPES = {
    "calendar",
    "im",
    "docs",
    "wiki",
    "base",
    "tasks",
    "minutes",
    "vc",
    "mail",
    "okr",
    "approval",
    "report_cache",
}

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

STATUS_LABELS = {
    "completed": "已完成",
    "in_progress": "进行中",
    "blocked": "阻塞",
    "planned": "计划中",
}
