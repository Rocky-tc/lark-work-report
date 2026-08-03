# 正文抓取契约

仅在 `stage=fetch` 时读取本文件。每个 `item_ref` 必须恰好产生一个完整结果；不得摘要、截断或抽样。

- `output.mode=managed_file` 时，让适配器把结果原子写入包内 `result_file`。阶段结果只回传对应 `batch_ref`，不要复制正文。
- 没有文件输出能力时，在阶段结果的 `batches[].results` 内提交完整正文。
- 正文结果字段为 `item_ref`、`content_type=text|xml|json`、`content`、`context`。
- 适配器明确返回无权限、已删除或不可访问时，提交 `item_ref`、`outcome=access_gap`、`reason`；不得把错误文本伪装成正文。
- 同包只在 `parallel=true` 时并行。提交前必须恰好覆盖所有批次和 item。

结果只含上述语义字段，并符合 `result_schema_file`；宿主不支持 Schema 时严格按上述字段构造。包 ID、输入摘要和提交由宿主私下处理，不写入结果。
