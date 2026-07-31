# 阶段输入输出接口

`stage-io` 是标准运行的唯一阶段入口。它把完整机器状态留在受管临时目录，只向 Agent 提供当前阶段所需内容和短引用；不要自行拼接队列、账本或最终报告。

## 调用循环

```bash
python3 scripts/stage-io.py next --run-dir <run-dir>
python3 scripts/stage-io.py commit \
  --run-dir <run-dir> \
  --result-file <stage-result.json>
```

`next` 返回 `fetch`、`extract`、`synthesize` 或 `complete`。前三种都带 `packet_file`、`package_id` 和 `input_digest`；只读取 `packet_file`。结果文件必须写在当前 `<run-dir>` 内，并原样带回包 ID 与输入摘要。接口会校验私有回执及源文件摘要；任何输入已变化的结果都拒绝写入。

## fetch

包内每批包含 `batch_ref`、适配器、操作和若干 `item_ref`。`locator` 是适配器读取对象所需的最小稳定定位信息。调用前按实际批次数预留预算；同包仅在 `parallel=true` 时并行。

每个 item 必须完整读取，结果严格为：

```json
{
  "schema_version": 1,
  "package_id": "fetch-...",
  "input_digest": "sha256:...",
  "batches": [{
    "batch_ref": "b0",
    "results": [{
      "item_ref": "i0",
      "content_type": "text",
      "content": "完整正文",
      "context": {}
    }]
  }]
}
```

`content_type` 只允许 `text`、`xml`、`json`。每批必须恰好覆盖包内全部 item。提交后接口恢复全局稳定 ID，写入原始正文并生成无损规范化正文；完全相同内容只存一份，候选上下文仍逐项保留。

## extract

包用 `cN` 引用无损正文，用 `iN` 引用候选；不会暴露全局 ID、来源链接和正文哈希。必须处理每个 item，结果之一为：

```json
{"item_ref":"i0","outcome":"work","record":{}}
{"item_ref":"i1","outcome":"uncertain","record":{}}
{"item_ref":"i2","outcome":"discarded_private"}
{"item_ref":"i3","outcome":"discarded_chatter"}
{"item_ref":"i4","outcome":"access_gap","reason":"无读取权限"}
```

`record` 只写语义事实，不得写 `record_id`、`source_type`、`source_ref`、`occurred_at`、`work_relevance` 或 `retention_mode`；这些字段由接口从候选和运行状态回填。完整字段语义见 [extraction-contract.md](extraction-contract.md)。

外层结构与 fetch 相同：保留 `schema_version`、`package_id`、`input_digest` 和按 `batch_ref` 分组的 `batches[].results`。提交后接口检查逐项覆盖、结果类型、字段白名单和确定性身份。

## synthesize

包只含紧凑的 `work`、`uncertain`、账本指纹及必要周期上下文。`work` 项使用 `w0`、`w1`；`uncertain` 项使用 `u0`、`u1`。不要发明或改写引用。

提交报告模型 v5：

```json
{
  "schema_version": 1,
  "package_id": "synthesize-...",
  "input_digest": "sha256:...",
  "ledger_fingerprint": "sha256:...",
  "summary": [{"result":"形成可核验产出","evidence_refs":["w0"]}],
  "workstreams": [],
  "risks": [],
  "next_actions": [],
  "uncertain": []
}
```

所有工作与待复核账本项至少被同类型章节引用一次。模型不写 `profile`、`title`、`coverage`、真实证据 ID、来源、排序依据或行动事实；接口从受管的运行计划、身份、审计与完整账本回填，并一次完成渲染和校验。

## 失败原则

- 不修改包文件、私有回执、队列、账本或确定性字段来绕过错误。
- 短引用未知、重复、跨分区，或结果未完整覆盖当前包时停止。
- 包生成后任何受保护输入变化时重新执行 `next`，不得继续提交旧结果。
- `complete` 前不得把中间模型当作最终报告；最终交付只使用返回的 `report_file`。
