# 六段式确定性输出

## 目的

日报、周报、月报共用一套六段式信息结构。智能体负责从证据账本形成结构化报告模型，`render-report.py` 负责排序、字段顺序、章节名称、空章节和 Markdown 输出。

不要绕过渲染器直接自由撰写最终报告。

存在 `<run-dir>/template-profile.json` 时，定稿器先按 [template-profiles.md](template-profiles.md) 校验档案，再替换标题结构、章节显示名称、条目形式、字段标签和工作流分组。六个语义槽、事实字段、来源锚和结果优先顺序保持不变；没有模板时输出必须与内置格式一致。

## 六层信息

| 顺序 | 语义 | 日报 | 周报 | 月报 |
|---|---|---|---|---|
| 1 | 摘要 | 今日摘要 | 本周摘要 | 月度摘要 |
| 2 | 进展和结果 | 工作进展与结果 | 工作流进展与结果 | 目标与工作流进展 |
| 3 | 风险 | 风险与需协助事项 | 风险与需协助事项 | 风险与依赖 |
| 4 | 下一周期 | 明日重点 | 下周重点 | 下月重点 |
| 5 | 待复核 | 待复核 | 待复核 | 待复核 |
| 6 | 证据覆盖 | 来源与覆盖 | 来源与覆盖 | 来源与覆盖 |

## 报告模型

```json
{
  "schema_version": 1,
  "profile": "weekly",
  "title": "张三个人周报｜2026-07-20 至 2026-07-26",
  "summary": [
    {
      "priority": 1,
      "result": "完成报告 Skill 的通用化升级",
      "impact": "任意兼容宿主都可按统一契约调用",
      "source_ref": "https://example.com/summary"
    }
  ],
  "workstreams": [
    {
      "priority": 1,
      "name": "报告 Skill",
      "status": "completed",
      "result": "形成六段式报告输出",
      "impact": "减少重复章节",
      "decision": "使用结构化模型统一渲染",
      "progress": "渲染器和校验器已通过测试",
      "source_ref": "source://host.lark/docs/doc-1"
    }
  ],
  "risks": [],
  "next_actions": [
    {
      "priority": 1,
      "action": "使用真实周报做可读性验证",
      "purpose": "确认重要信息能在第一屏被识别"
    }
  ],
  "uncertain": [],
  "coverage": {
    "start": "2026-07-20T00:00:00+08:00",
    "end": "2026-07-26T18:00:00+08:00",
    "snapshot": "2026-07-26T18:00:00+08:00",
    "domains": ["日历", "消息", "文档", "任务", "会议"],
    "access_gaps": [],
    "work_count": 12,
    "uncertain_count": 0
  }
}
```

## 字段规则

- `schema_version`：固定为 `1`。渲染器拒绝缺失或未知版本。
- 报告模型和各层对象采用严格字段白名单；字段拼写错误或未知字段直接报错，不静默忽略。
- `priority`：非负整数，数值越小越靠前；相同值保持输入顺序。不设置时按 `100` 处理。
- `summary[].result`：先写已经产生的结果；可选 `impact`、`decision` 按结果、影响、决策的顺序输出。
- `workstreams[]`：必填 `name`、`status`、`result`、`source_ref`；可选 `impact`、`decision`、`progress`。渲染顺序固定为结果、影响、决策、进展。
- `status`：仅允许 `completed`、`in_progress`、`blocked`、`planned`。
- `risks[]`：必填 `risk`、`source_ref`；可选 `impact`、`assistance`。
- `next_actions[]`：必填 `action`；可选 `purpose`，不强制来源。
- `uncertain[]`：必填 `description`、`reason`、`source_ref`。
- `source_ref`：只允许不会破坏 Markdown 链接的 `http://`、`https://` 或 `source://`；括号必须进行 URL 编码。
- 空数组渲染为单行 `- 无`，不补充解释性套话。

## 命令

从 Skill 根目录运行：

```bash
python3 scripts/render-report.py \
  --file <run-dir>/report-model.json \
  --template-file <run-dir>/template-profile.json \
  --context-file <run-dir>/template-context.json \
  --output <run-dir>/report.md
```

模板和上下文参数均可选；无模板时不要创建空档案。标准流程使用 `finalize-run.py` 从 `identity.json` 与 `run-plan.json` 构造标题上下文，并一次完成渲染与账本校验；`render-report.py` 和 `validate-report.py` 是排错时使用的底层命令。报告中的每个工作来源必须存在于 `work` 账本，每个待复核来源必须存在于 `uncertain` 账本。
