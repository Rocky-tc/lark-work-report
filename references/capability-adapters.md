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
      "domains": ["calendar", "im", "docs", "tasks"]
    },
    "candidate.list_many": {
      "available": true,
      "max_domains": 4
    },
    "candidate.fetch": {"available": true},
    "candidate.fetch_many": {
      "available": true,
      "max_batch_size": 20
    },
    "report.create": {"available": true},
    "report.fetch": {"available": true}
  }
}
```

`adapter_id` 在本次运行中必须稳定。规范来源类型只有：

`calendar`、`im`、`docs`、`wiki`、`base`、`tasks`、`minutes`、`vc`、`mail`、`okr`、`approval`、`report_cache`。

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

可选元数据为 `title`、`container`、`participants`、`workstream_hint`、`priority`、`cursor`。候选不得包含 `body`、`content`、`raw_content`、`transcript`、`message_text`、`full_text` 或等价正文。

`metadata_only=true` 表示广泛列举不会读取正文。只有同时具备元数据列举和独立正文读取时，校验器才返回 `collection_mode=broad`。

`parallel_safe=true` 表示不同数据域可以并行列举；`max_parallelism` 是适配器允许的单波并发数。若不能保证限流、游标和会话彼此隔离，不得声明。

### `candidate.list_many`

一次请求列举多个数据域的元数据。`max_domains` 是单次请求可接受的数据域上限。该能力必须建立在 `candidate.list.metadata_only=true` 之上；如果不可用，运行计划再选择并行或串行的 `candidate.list`。

### `candidate.fetch`

只按已经通过队列过滤器的稳定 ID 读取必要正文和上下文。结果必须同时返回可核验来源标识。无稳定链接时使用：

```text
source://<adapter_id>/<source_type>/<stable_id>
```

### `candidate.fetch_many`

一次请求按多个已经过过滤与去重的稳定 ID 抓取正文。`max_batch_size` 是单次请求上限。结果必须保持输入项与来源锚的一一对应；部分失败要逐项返回，不能静默漏项。

### `report.create` 与 `report.fetch`

创建操作返回文档稳定 ID 和 URL；回读操作按此 ID 返回标题与正文。创建而不能回读不构成可靠交付，因此校验器会报错。两者同时可用时 `delivery_mode=document`，否则为 `markdown`。

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

不要切换全局身份、修改全局配置或扩大权限。不同数据域可以混用适配器，但每条候选必须保留自己的 `adapter_id`，并在覆盖说明中记录实际来源和能力缺口。

## 调用计数

- 每次外部工具、MCP 或 CLI 请求计 1 次。
- 重试计入预算。
- 本地脚本和对已返回结果的本地分析不计。
- 一次请求返回多个域仍计 1 次，但分别记录覆盖域。

单次调用可用 `manage-run.py record-call` 记账；批量或并行波次在执行前用 `record-batch` 一次原子预留全部调用。硬上限拒绝后停止扩散。
