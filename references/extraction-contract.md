# 正文提取契约

仅在 `stage=extract` 时读取本文件。

## 输入与覆盖

- 完整读取当前包中每个 item 的正文和上下文，并恰好返回一个结果。长正文可完整分块，不得用摘要、截断或抽样代替。
- 顶层 `content_blobs` / `context_blobs` 只共享字节或规范 JSON 完全一致的值；即使共享正文，每个 item 仍必须独立分类并覆盖。

## 结果类型

- 内容只分四类：`work`、`uncertain`、`discarded_private`、`discarded_chatter`。`work` / `uncertain` 必须带 `record`；后两类只交 `item_ref` 和 `outcome`。
- 无权限、已删除或不可访问不是内容分类，交最小 `access_gap`：`item_ref`、`outcome`、`reason`。
- 确认为私人或闲聊时，不得保存原文、摘要、参与人、标题或链接。

## 证据记录

记录只写语义事实，字段白名单为：

`actor`、`title`、`workstream`、`activity`、`status`、`status_basis`、`output`、`impact`、`decision`、`risk`、`next_action`、`signal_kind`、`action_kind`、`starts_at`、`ends_at`、`due_at`、`requires_response`、`assignee_relation`、`participants`、`confidence`、`sensitivity`、`classification_reason`。

`record_id`、`source_type`、`source_ref`、`occurred_at`、`work_relevance` 和 `retention_mode` 由接口回填，Agent 不得输出。

候选 `metadata` 已有的 `starts_at`、`ends_at`、`due_at`、`requires_response`、`action_kind`、`assignee_relation` 也由接口回填，Agent 应省略；旧宿主重复提交完全相同的值可接受，任何冲突都拒绝整项。未提供的字段可依完整正文填写；`title`、`participants`、`workstream_hint` 不是权威回填字段，仍须核验正文。

不得从参会、编辑或讨论推断产出与影响；结果、完成状态、指标和决策没有直接证据时降级为 `unconfirmed` 或留空。`uncertain` 不得作为成果、结果、完成状态、决策或影响的依据。

## 提交

使用当前包的短引用，恰好覆盖全部 item：

```json
{"results":[{"item_ref":"i0","outcome":"work","record":{}}]}
```

结果不写 `schema_version`、`package_id` 或 `input_digest`。宿主不支持 `result_schema_file` 时，仍须严格使用上述结果类型、字段白名单和包装结构，不得增加未知字段。修复只处理返回包指定的 item，不重处理已验证项。

需要排查字段、状态冲突或聚类问题时，再读取 [evidence-model.md](evidence-model.md)。
