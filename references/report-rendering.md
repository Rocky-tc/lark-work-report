# 五段式确定性输出

## 目的

日报、周报、月报共用一套五段式正文结构。智能体负责从证据账本形成结构化报告模型，`render-report.py` 负责排序、字段顺序、章节名称、空章节和 Markdown 输出。

不要绕过渲染器直接自由撰写最终报告。

存在 `<run-dir>/template-profile.json` 时，定稿器先按 [template-profiles.md](template-profiles.md) 校验档案，再替换标题结构、章节显示名称、条目形式、字段标签和工作流分组。五个正文语义槽、事实字段、来源锚和结果优先顺序保持不变；没有模板时输出必须与内置格式一致。

## 无损紧凑规则

- 完整 URL 在同一报告出现两次以上时使用 `W1` / `U1` Markdown 引用，正文写 `[W1][W1]`，定义在文末只出现一次且不增加章节；只出现一次的来源继续使用行内链接，避免来源索引反而变长。
- `W` 只能支撑工作章节，`U` 只能支撑待复核。短引用必须有且只有一个定义，定义必须被使用并能解析到对应账本。
- 摘要的核心 `result` 始终保留；可选 `impact` 或 `decision` 与详细章节中的事实逐字相同时，只在详细章节展示一次。不同措辞不会自动合并。
- 覆盖范围、快照、覆盖域、权限缺口和 work/uncertain 计数始终保留在报告模型和验收中。没有缺口时不渲染；存在访问缺口时，文末显示一条无标题说明；没有任何可用数据源时也必须说明。
- 这些规则位于确定性渲染层，对内置格式和个人模板同时生效，不改变报告模型、证据覆盖或五段正文语义。

## 五层正文

| 顺序 | 语义 | 日报 | 周报 | 月报 |
|---|---|---|---|---|
| 1 | 摘要 | 今日摘要 | 本周摘要 | 月度摘要 |
| 2 | 进展和结果 | 工作进展与结果 | 工作流进展与结果 | 目标与工作流进展 |
| 3 | 风险 | 风险与需协助事项 | 风险与需协助事项 | 风险与依赖 |
| 4 | 下一周期 | 明日重点 | 下周重点 | 下月重点 |
| 5 | 待复核 | 待复核 | 待复核 | 待复核 |

## 报告模型

标准阶段接口新建模型 v5。Agent 只提交五类叙事数组和 `wN` / `uN` 短引用；接口在落盘模型中私下注入账本指纹。综合包为安全简单项声明目标 `direct_sections` 时，对应报告条目可以只提交引用：

```json
{
  "summary": [{
    "evidence_refs": ["w0"]
  }],
  "workstreams": [],
  "risks": [],
  "next_actions": [],
  "uncertain": []
}
```

只有单一引用对应单记录、单来源、无状态冲突、账本字段足够、目标章节列在 `direct_sections` 且未启用个人模板时，接口才允许确定性回填。Agent 可以只交引用，也可以只交可选影响、决策等新增叙事，接口补齐缺失必填字段；多来源归纳、冲突项和个人模板文风继续由模型写作。`stage-io` 先注入账本指纹并把短引用展开为真实 `evidence_ids`，再回填 `profile`、标题、覆盖信息、优先级、来源、排序依据与行动事实。

```json
{
  "schema_version": 3,
  "profile": "weekly",
  "title": "张三个人周报｜2026-07-20 至 2026-07-26",
  "summary": [
    {
      "priority": 1,
      "evidence_ids": ["sha256:summary-cluster"],
      "result": "完成报告 Skill 的通用化升级",
      "impact": "任意兼容宿主都可按统一契约调用",
      "priority_basis": "有明确影响、多来源印证",
      "source_refs": [
        "https://example.com/summary",
        "https://example.com/comment"
      ]
    }
  ],
  "workstreams": [
    {
      "priority": 1,
      "evidence_ids": ["sha256:workstream-cluster"],
      "name": "报告 Skill",
      "status": "completed",
      "result": "形成五段式报告输出",
      "impact": "减少重复章节",
      "decision": "使用结构化模型统一渲染",
      "progress": "渲染器和校验器已通过测试",
      "source_refs": [
        "source://host.lark/docs/doc-1",
        "source://host.lark/comments/comment-1"
      ]
    }
  ],
  "risks": [],
  "next_actions": [
    {
      "priority": 1,
      "evidence_ids": ["sha256:action-cluster"],
      "action": "使用真实周报做可读性验证",
      "purpose": "确认重要信息能在第一屏被识别",
      "action_kind": "review",
      "due_at": "2026-07-28T18:00:00+08:00",
      "assignee_relation": "self",
      "priority_basis": "当前主体责任明确、两天内到期",
      "source_refs": ["source://host.lark/comments/comment-1"]
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

- `schema_version=1`、`2`、`3` 和 `4` 用于兼容旧流程；标准阶段接口新建报告使用 `5`。
- v5 每个非空条目必须提供一个或多个唯一 `evidence_refs`。摘要、进展、风险和下一周期只允许 `wN`；待复核只允许 `uN`。私有账本指纹不匹配、短引用越界或跨分区时拒绝定稿。
- v5 条目默认仍须满足对应章节的叙事字段要求。仅当条目只含一个 `evidence_ref`，且对应输入项明确把目标章节列入 `direct_sections` 时，才可省略全部叙事或仅提交可选叙事；接口只补必填字段。多记录、多来源、多引用、冲突或个人模板模式一律要求模型完整叙事。
- v5 禁止写入可由运行状态或账本回填的顶层字段和事实字段；v4 使用真实 `evidence_ids`，并继续由定稿器回填条目级确定事实。
- `evidence_ids` 必须引用完整账本中同类型聚合项的 `cluster_id`。摘要、进展、风险和下一周期只能引用 `work`；待复核只能引用 `uncertain`。定稿器要求工作与待复核账本中的每个聚合项至少被引用一次。
- 报告模型和各层对象采用严格字段白名单；字段拼写错误或未知字段直接报错，不静默忽略。
- `priority`：非负整数，数值越小越靠前；相同值保持输入顺序。不设置时按 `100` 处理。
- v2 可选 `priority_basis`，只写最重要的可解释依据，不展示内部数值分数。
- `summary[].result`：先写已经产生的结果；可选 `impact`、`decision` 按结果、影响、决策的顺序输出。
- `workstreams[]`：必填 `name`、`status`、`result` 和至少一个来源；可选 `impact`、`decision`、`progress`。渲染顺序固定为结果、影响、决策、进展、排序依据。
- `status`：仅允许 `completed`、`in_progress`、`blocked`、`planned`。
- `risks[]`：必填 `risk` 和至少一个来源；可选 `impact`、`assistance`。
- `next_actions[]`：必填 `action`；可选 `purpose`。v2 还允许 `action_kind`、`starts_at`、`ends_at`、`due_at`、`requires_response`、`assignee_relation` 和 `priority_basis`；出现这些事实字段时必须提供工作来源。
- `uncertain[]`：必填 `description`、`reason` 和至少一个待复核来源。
- v1 使用 `source_ref`；v2 可使用 `source_ref` 或 `source_refs`，并为聚合事项输出全部可核验来源。来源只允许不会破坏 Markdown 链接的 `http://`、`https://` 或 `source://`。
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
