# 正文提取契约

仅在抓取队列形成后、读取完整正文并产生证据结果时读取本文件。

## 完整性

- 每个入队 item 必须完整读取正文，并恰好产生一个结果。
- 长正文可完整分块处理，不得只读摘要、截断片段或抽样。
- 提取读取由各批次 `semantic_file` 无损重组的当前包。顶层 `content_blobs` 与 `context_blobs` 只对字节或规范 JSON 完全一致的值做精确引用；每个候选仍独立产生语义结果。
- 结果只能是带证据记录的 `work` / `uncertain`，或最小结果 `discarded_private`、`discarded_chatter`、`access_gap`。
- 正文确认属于私人或闲聊时不得保存原文、摘要、参与人、标题或链接。

## 证据记录

记录只写语义事实。允许字段和枚举由编译器严格校验，核心语义包括：

`actor`、`title`、`workstream`、`activity`、`status`、`status_basis`、`output`、`impact`、`decision`、`risk`、`next_action`、`signal_kind`、`action_kind`、`starts_at`、`ends_at`、`due_at`、`requires_response`、`assignee_relation`、`participants`、`confidence`、`sensitivity`、`classification_reason`。

`record_id`、`source_type`、`source_ref`、`occurred_at`、`work_relevance` 和 `retention_mode` 由 `stage-io` 根据私有回执确定性回填，Agent 不得重复输出。

候选 `metadata` 已出现的 `starts_at`、`ends_at`、`due_at`、`requires_response`、`action_kind` 和 `assignee_relation` 也由接口回填，Agent 应省略；为兼容旧宿主，重复提交完全相同的值仍可接受，任何冲突都会拒绝整项结果。候选未提供的字段仍可根据完整正文填写。`title`、`participants` 和 `workstream_hint` 不属于这组权威回填字段，不能仅因元数据存在就代替正文语义核验。

不得从参会、编辑或讨论推断产出与影响；结果、完成状态、指标和决策没有直接证据时降级为 `unconfirmed` 或留空。`uncertain` 不得作为成果、结果、完成状态、决策或影响的依据。

使用当前包顶层短引用提交，必须恰好覆盖全部 item：

```json
{"results":[{"item_ref":"i0","outcome":"work","record":{}}]}
```

结果不写 `schema_version`、`package_id` 或 `input_digest`；私有关联、身份恢复和证据写入由接口完成。修复只处理 `repair_packet` 或 `repair-queue.json` 指定的候选，不得重新采集已验证项。

需要排查字段、状态冲突或聚类问题时，再读取 [evidence-model.md](evidence-model.md)。
