# 双层证据模型

## 第一层：元数据候选与抓取队列

每条元数据候选至少包含：

| 字段 | 含义 |
|---|---|
| `id` | 本次运行内唯一 ID |
| `occurred_at` | 行为发生或资料更新时间 |
| `source_type` | calendar、message、document、task、meeting 等 |
| `source_ref` | 可核验的链接、ID 或消息锚 |
| `prefetch_relevance` | work、uncertain、private、chatter |
| `classification_reason` | 仅基于元数据的预分类理由 |

运行 `prepare-fetch-queue.py` 后，正文抓取队列只保留 `work` 与 `uncertain`。`private`、`chatter` 不得出现在队列输出中。

## 第二层：正文核验记录

只为抓取队列中的候选补充：

| 字段 | 含义 |
|---|---|
| `actor` | 行为主体 |
| `workstream` | 项目、客户、产品或工作流 |
| `activity` | 做了什么 |
| `status` | completed、in_progress、blocked、unconfirmed |
| `output` | 文档、功能、决策或交付物 |
| `impact` | 有证据支持的结果 |
| `decision` | 决策及状态 |
| `risk` | 风险、依赖或未决问题 |
| `next_action` | 下一步 |
| `confidence` | high、medium、low |
| `work_relevance` | work 或 uncertain |
| `sensitivity` | normal、internal_sensitive、private |
| `classification_reason` | 可复核的分类信号 |
| `retention_mode` | ephemeral 或 persistent |

正文发现候选实际属于私人或闲聊时，删除该记录，不转存为第三、第四类正文记录。

## 第三层：工作与待复核账本

- 工作证据账本：只含 `work_relevance=work`，支撑报告事实和结论。
- 待复核账本：只含 `work_relevance=uncertain`，只渲染到“待复核”章节。

待复核账本不能被分析器用于生成成果、结果、完成状态、决策或影响。

## 去重

按以下顺序：

1. 文档 token、消息 ID、会议 ID、任务 ID 精确去重；
2. 同一来源链接去重；
3. 时间邻近、参与人、工作流和事件语义合并；
4. 日报、周报与原始来源重复时，保留报告作为缓存引用，原始来源作为事实锚。

不能因为两条文字相似就合并不同里程碑，也不能把多次状态更新重复计为多项产出。

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
- 新状态不自动覆盖旧状态；先确认是否是同一对象、同一时间点和同一口径。
- 来源冲突时标为 `unresolved`，报告写明差异和待确认项。
- 结果、影响、指标、完成状态和重要决策必须有直接来源；否则降级为 `unconfirmed` 或删除。
- 不从“参加会议”“编辑文档”推断业务影响。

## 推荐中间表示

```json
{
  "workstream": "个人工作报告 Skill",
  "activity": "完成设计评审",
  "status": "completed",
  "output": "设计规格",
  "impact": null,
  "confidence": "high",
  "work_relevance": "work",
  "source_ref": "https://example.feishu.cn/docx/..."
}
```

`impact=null` 应保持为空，不能改写成未经证实的价值判断。
