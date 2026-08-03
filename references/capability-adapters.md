# 能力适配契约

## 目标

核心流程只依赖统一语义，不依赖某个宿主的工具名。适配器可以是原生连接器、MCP、CLI 或导出文件。

适配器在采集前必须声明机器可读 manifest。结构定义见 [adapter-contract.schema.json](adapter-contract.schema.json)，并用以下命令校验：

```bash
python3 scripts/validate-adapter.py --file <adapter-manifest.json>
```

## 最小 manifest

```json
{
  "adapter_id": "host.lark",
  "capabilities": {
    "identity.current": {"available": true},
    "candidate.list": {
      "available": true,
      "metadata_only": true,
      "parallel_safe": true,
      "max_parallelism": 4,
      "domains": ["calendar", "im", "docs", "tasks"],
      "domain_coverage": {
        "im": ["im", "mentions"],
        "docs": ["docs", "comments"]
      }
    },
    "candidate.list_many": {
      "available": true,
      "max_domains": 4
    },
    "candidate.fetch": {
      "available": true,
      "file_output": true,
      "parallel_safe": true,
      "max_parallelism": 4
    },
    "candidate.fetch_many": {
      "available": true,
      "max_batch_size": 20
    },
    "report.create": {"available": true},
    "report.fetch": {"available": true},
    "template.fetch": {"available": true},
    "template.upsert": {"available": true},
    "template.delete": {"available": true}
  }
}
```

`adapter_id` 在本次运行中必须稳定。规范来源类型只有：

`calendar`、`im`、`mentions`、`docs`、`comments`、`wiki`、`base`、`tasks`、`minutes`、`vc`、`mail`、`okr`、`approval`、`report_cache`、`code_activity`、`ai_sessions`。

`code_activity` 与 `ai_sessions` 是可选来源，不得成为飞书客户使用本 Skill 的前置条件。

## 能力语义

### `identity.current`

返回当前主体的稳定标识、展示名称和时区。无法取得稳定标识时使用“当前用户”，但不得把其他成员活动归到当前用户。

实时列举或读取候选时此能力必须可用；离线导出模式可以由用户提供主体与时区。
`prepare-run.py` 会把该操作写入 `identity_call`；宿主必须先执行并计量，再开始元数据波次。

### `candidate.list`

按数据域和左闭右开时间窗列出候选元数据。每条候选至少包含：

- `id`：当前适配器内稳定且可用于读取；
- `adapter_id`；
- `source_type`；
- `occurred_at`：带时区的 ISO 8601；
- `source_ref`：可核验链接或 `source://` 稳定来源；
- `prefetch_relevance`；
- `classification_reason`。

可选元数据为 `title`、`container`、`participants`、`workstream_hint`、`priority`、`cursor`、`starts_at`、`ends_at`、`due_at`、`requires_response`、`action_kind`、`assignee_relation`。时间必须带时区；行动类型和责任关系使用 [evidence-model.md](evidence-model.md) 的枚举。适配器只有在字段由来源直接给出时才返回 `starts_at`、`ends_at`、`due_at`、`requires_response`、`action_kind` 或 `assignee_relation`，不得从参会、@、编辑或讨论行为推断。阶段接口会确定性回填这些已声明事实；未提供的字段继续由正文语义核验。候选不得包含 `body`、`content`、`raw_content`、`transcript`、`message_text`、`full_text` 或等价正文。

`domains` 表示适配器真正需要调用的查询域，`domain_coverage` 可声明一次查询返回的证据类型。键必须属于 `domains`，值必须包含键自身。例如 `im → [im, mentions]` 表示查询消息时已同时覆盖 @ 提及，规划器不会再单独查询 `mentions`。未声明时按一对一覆盖处理。

`metadata_only=true` 表示广泛列举不会读取正文。只有同时具备元数据列举和独立正文读取时，校验器才返回 `collection_mode=broad`。

`parallel_safe=true` 表示不同数据域可以并行列举；`max_parallelism` 是适配器允许的单波并发数。若不能保证限流、游标和会话彼此隔离，不得声明。

### `candidate.list_many`

一次请求列举多个数据域的元数据。`max_domains` 是单次请求可接受的数据域上限。该能力必须建立在 `candidate.list.metadata_only=true` 之上；如果不可用，运行计划再选择并行或串行的 `candidate.list`。

### `candidate.fetch`

只按已经通过队列过滤器的稳定 ID 读取完整正文和上下文。长材料可以完整分块，但不得用摘要、搜索片段或截断结果替代正文核验。结果必须同时返回可核验来源标识。无稳定链接时使用：

```text
source://<adapter_id>/<source_type>/<stable_id>
```

`file_output=true` 表示适配器能把以当前 `batch_ref` / `item_ref` 标识的完整结果直接写入 fetch 包指定的受管 `result_file`；阶段提交只回 `batch_ref`，正文不经过 Agent 结果。接口校验后再私下恢复稳定 ID，写入标准 `body_file` 并生成无损语义文件。适配器明确得到无权限、已删除或不可访问时写 `outcome=access_gap` 与最小 `reason`，不得伪造空正文。没有文件输出能力时使用同一字段结构内联提交。`parallel_safe=true` 与 `max_parallelism` 表示不同抓取批次可按受控波次并行；未明确声明时必须串行。

正文核验结果按 [evidence-model.md](evidence-model.md) 转换为逐项提取结果。新增来源只需新增适配器映射，不得修改归并器或报告渲染逻辑。

### `candidate.fetch_many`

一次请求按多个已经过过滤与去重的稳定 ID 抓取正文。`max_batch_size` 是单次请求上限。结果必须保持输入项与来源锚的一一对应；部分失败要逐项返回，不能静默漏项。

### `report.create` 与 `report.fetch`

创建操作返回文档稳定 ID 和 URL；回读操作按此 ID 返回标题与正文。创建而不能回读不构成可靠交付，因此校验器会报错。两者同时可用时 `delivery_mode=document`，否则为 `markdown`。

### `template.fetch`、`template.upsert` 与 `template.delete`

三个操作按当前主体稳定 ID 和固定 `template_id=default` 管理一份个人模板档案：

- `template.fetch` 返回规范化的 `template-profile.json` 与存储版本；不存在时返回明确的 not-found，不创建空模板。
- `template.upsert` 原子新增或替换档案。写入成功后必须再次 `template.fetch`，确认回读内容与规范化档案一致。
- `template.delete` 删除默认模板。删除后必须再次 `template.fetch`，确认模板不存在。

`template.upsert` 与 `template.delete` 必须同时可用，并依赖 `template.fetch` 完成验收。只提供 `template.fetch` 时为 `template_mode=read_only`；三者齐全时为 `read_write`。模板档案契约见 [template-profiles.md](template-profiles.md)。

## 安全降级

| 校验结果 | 允许行为 |
|---|---|
| `broad` | 可按预算进行元数据广泛发现，再定向抓正文 |
| `explicit_only` | 只读用户点名对象、已知稳定 ID 或已有报告引用 |
| `offline_only` | 只处理用户提供的导出材料 |

搜索与读取合并为一次调用、或“搜索”会直接返回正文时，必须是 `explicit_only`。不能因为宿主缺少两段式接口，就在广泛搜索中被动读取已经能确定为私人或闲聊的正文。

## 实现选择

优先选择已经获用户授权且能完成任务的实现：

1. 宿主已连接的飞书工具或 MCP；
2. 已安装并认证的 CLI；
3. 用户提供的 Markdown、JSON、CSV、文档或消息导出。

不要切换全局身份、修改全局配置或扩大权限。不同数据域可以混用适配器，但每条候选必须保留自己的 `adapter_id`，并在内部覆盖记录中保存实际来源和能力缺口；存在访问缺口时再条件性显示说明。

## 调用计数

- 每次外部工具、MCP 或 CLI 请求计 1 次。
- 重试计入预算。
- 本地脚本和对已返回结果的本地分析不计。
- 一次请求返回多个域仍计 1 次，但分别记录覆盖域。
- 持久模板启用时，每次报告最多增加 1 次 `template.fetch`，并单独增加 1 次硬上限，不减少采集预算。
- 保存模板通常为“读取样例 + upsert + fetch 验收”2–3 次；重置为 delete + fetch 验收 2 次。

单次调用可用 `manage-run.py record-call` 记账；批量或并行波次在执行前用 `record-batch` 一次原子预留全部调用。硬上限拒绝后停止扩散。
