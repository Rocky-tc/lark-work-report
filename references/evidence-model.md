# 三层证据与跨源归并模型

## 第一层：元数据候选与抓取队列

候选字段以 [capability-adapters.md](capability-adapters.md) 为准，只允许元数据，不得包含正文或逐字稿。`prepare-fetch-queue.py` 只保留 `work` 与 `uncertain`，生成全局稳定 ID，并按 `source_ref` 跨域去重；重复来源的其他稳定 ID 记入 `alternate_ids`。队列以 `candidate_index` 单点保存候选元数据，批次只引用 `global_ids`；执行时逐批读取生成的请求文件。私人、闲聊及其分类计数不得进入队列文件或终端摘要。

## 第二层：正文核验记录

只为抓取队列中的候选补充：

| 字段 | 含义 |
|---|---|
| `actor` | 行为主体 |
| `title` | 来源中的事项标题 |
| `workstream` | 项目、客户、产品或工作流 |
| `activity` | 做了什么 |
| `status` | completed、in_progress、blocked、planned、unconfirmed |
| `status_basis` | explicit、stated_plan、inferred |
| `output` | 文档、功能、决策或交付物 |
| `impact` | 有证据支持的结果 |
| `decision` | 决策及状态 |
| `risk` | 风险、依赖或未决问题 |
| `next_action` | 下一步 |
| `signal_kind` | outcome、action、decision、risk、event |
| `action_kind` | reply、prepare、review、deliver、follow_up、attend |
| `occurred_at` | 事实发生或状态证据时间 |
| `starts_at` / `ends_at` | 日程或事件时间 |
| `due_at` | 截止时间 |
| `requires_response` | 是否需要当前主体回复 |
| `assignee_relation` | self、shared、unknown |
| `participants` | 相关人员的稳定标识或可核验名称 |
| `confidence` | high、medium、low |
| `work_relevance` | work 或 uncertain |
| `sensitivity` | normal、internal_sensitive、private |
| `classification_reason` | 可复核的分类信号 |
| `retention_mode` | ephemeral 或 persistent |

正文发现候选实际属于私人或闲聊时，删除该记录，不转存为第三、第四类正文记录。

## 第三层：归并后的工作与待复核账本

每个抓取批次先写一个 `<run-dir>/evidence-parts/<batch-id>.json`。每个队列候选恰好有一个结果；工作与待复核结果带完整证据记录，确定的私人/闲聊仅保留丢弃类型，访问失败仅保留最小原因：

```json
{
  "schema_version": 1,
  "batch_id": "fetch-0001",
  "results": [
    {"global_id": "host:tasks:1", "outcome": "work", "record": {}},
    {"global_id": "host:im:2", "outcome": "discarded_chatter"}
  ]
}
```

再由完整性编译器校验全队列覆盖并生成 `evidence-records.json`、`ledger.json` 和不含私人/闲聊详情的 `extraction-audit.json`：

```bash
python3 scripts/compile-evidence.py --run-dir <run-dir>
```

编译器拒绝缺失、重复、未知候选、来源身份错配、未知字段及非法结果；只有全部候选处理完成才原子产出下游文件。证据记录只允许 `work` 与 `uncertain`；`sensitivity=private` 会被归并器拒绝。终端只返回路径和计数，不返回候选正文。

- 工作证据账本：只含 `work_relevance=work`，支撑报告事实和结论。
- 待复核账本：只含 `work_relevance=uncertain`，只渲染到“待复核”章节。
- 每个聚合事项保留规范 `source_ref` 和全部 `source_refs`；同一来源不能同时属于两个账本。

用于校验器的最小 JSON 结构为：

```json
{
  "schema_version": 2,
  "work": [{
    "source_ref": "source://host.lark/tasks/task-1",
    "source_refs": [
      "source://host.lark/tasks/task-1",
      "source://host.lark/comments/comment-1"
    ]
  }],
  "uncertain": [{"source_ref": "https://example.com/source"}]
}
```

待复核账本不能被分析器用于生成成果、结果、完成状态、决策或影响。

最终校验时，工作章节使用的每个行内 `[工作来源]` 或 `Wn` 短引用必须能在工作账本中找到完全相同的 `source_ref`；`[待复核来源]` 与 `Un` 同理。仅构造格式正确但账本中不存在的链接或短引用不能通过校验。

## 报告模型

双账本核验完成后，`stage-io` 生成不含机器长标识和来源链接的综合包。Agent 用 `wN` / `uN` 短引用建立临时报告模型 v5；接口再把它们展开为账本 `cluster_id`。每个账本聚合项至少被一个相同类型的报告条目覆盖。模型只写叙事字段；主体、周期、覆盖信息、来源、排序依据和行动事实由接口从受管状态回填。报告模型不是新的证据层，也不得新增账本中不存在的事实。完整字段契约见 [report-rendering.md](report-rendering.md)。

## 去重

归并器按以下顺序：

1. 文档 token、消息 ID、会议 ID、任务 ID 精确去重；
2. 同一来源链接去重；
3. 仅在同一账本内，根据工作流、事项文本、截止时间和参与关系进行高阈值语义合并；
4. 日报、周报与原始来源重复时，保留报告作为缓存引用，原始来源作为事实锚。

工作与待复核不跨账本合并。不能因为两条文字相似就合并不同里程碑；无法确定是否相同时保留两条。归并后的事项保留全部来源和原始 `record_ids`，多次状态更新不重复计为多项产出。

## 工作流聚类

每个工作流至少包含：

- 名称与本周期目标；
- 关键活动；
- 产出与当前状态；
- 有证据支持的结果或影响；
- 决策、风险、依赖和下一步；
- 来源集合；
- 置信度和冲突状态。

日报以具体行动聚合；周报以工作流聚合；月报把持续工作流映射到目标和业务方向。聚合粒度变化不改变底层证据。

## 冲突与置信度

- 原始任务、会议、文档和消息通常优先于二手报告。
- 状态强度为 `explicit > stated_plan > inferred`；同强度时采用较新的明确状态。
- 较新的推断不能覆盖较旧的明确完成证据；“已讨论”和“计划完成”不能自动变成完成。
- 同强度、同时间出现不同状态时标记冲突，聚合事项自动进入待复核。
- 结果、影响、指标、完成状态和重要决策必须有直接来源；否则降级为 `unconfirmed` 或删除。
- 不从“参加会议”“编辑文档”推断业务影响。

## 报告重要性

归并器从已经核验的字段计算 `priority_score`、数值越小越靠前的 `priority` 和可读的 `priority_basis`。加权信号包括结果与影响、明确产出、关键决策、风险或阻塞、当前主体责任、截止紧迫度、待回复、证据置信度和多来源印证。

这是报告排序建议，不是用户或管理者确认的行动优先级。报告模型只选取最重要的排序依据进行展示，不得把分数改写为组织承诺。

## 推荐中间表示

```json
{
  "cluster_id": "sha256:...",
  "record_ids": ["task-1", "comment-1"],
  "workstream": "个人工作报告 Skill",
  "activity": "完成设计评审",
  "status": "completed",
  "output": "设计规格",
  "impact": null,
  "confidence": "high",
  "work_relevance": "work",
  "source_ref": "source://host.lark/tasks/task-1",
  "source_refs": [
    "source://host.lark/tasks/task-1",
    "source://host.lark/comments/comment-1"
  ],
  "priority": 20,
  "priority_score": 80,
  "priority_basis": ["有明确影响", "多来源相互印证"]
}
```

`impact=null` 应保持为空，不能改写成未经证实的价值判断。
