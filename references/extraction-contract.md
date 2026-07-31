# 正文提取契约

仅在抓取队列形成后、读取完整正文并产生证据结果时读取本文件。

## 完整性

- 每个入队 item 必须完整读取正文，并恰好产生一个结果。
- 长正文可完整分块处理，不得只读摘要、截断片段或抽样。
- 提取读取批次 `semantic_file`；它必须由原始 `body_file` 经 `normalize-fetch-body.py` 生成。规范化只压缩包装结构并精确去重完全相同内容，正文值与候选上下文不得丢失。
- 结果只能是带证据记录的 `work` / `uncertain`，或最小结果 `discarded_private`、`discarded_chatter`、`access_gap`。
- 正文确认属于私人或闲聊时不得保存原文、摘要、参与人、标题或链接。

## 证据记录

记录只写语义事实。允许字段和枚举由编译器严格校验，核心语义包括：

`actor`、`title`、`workstream`、`activity`、`status`、`status_basis`、`output`、`impact`、`decision`、`risk`、`next_action`、`signal_kind`、`action_kind`、`starts_at`、`ends_at`、`due_at`、`requires_response`、`assignee_relation`、`participants`、`confidence`、`sensitivity`、`classification_reason`。

`record_id`、`source_type`、`source_ref`、`occurred_at`、`work_relevance` 和 `retention_mode` 由 `stage-io` 根据私有回执确定性回填，Agent 不得重复输出。

不得从参会、编辑或讨论推断产出与影响；结果、完成状态、指标和决策没有直接证据时降级为 `unconfirmed` 或留空。`uncertain` 不得作为成果、结果、完成状态、决策或影响的依据。

每批用当前包的短引用提交，结构为：

```json
{"batch_ref":"b0","results":[{"item_ref":"i0","outcome":"work","record":{}}]}
```

外层 `schema_version`、`package_id` 与 `input_digest` 见 [stage-io.md](stage-io.md)。提交后由接口恢复全局身份并写入指定证据文件。编译失败时读取其返回的 `repair_file`，只处理其中的候选和批次；不得重新采集已验证通过的候选。终端只显示修复文件路径、数量和有界错误摘要。

需要排查字段、状态冲突或聚类问题时，再读取 [evidence-model.md](evidence-model.md)。
