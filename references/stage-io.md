# 阶段输入输出接口

`stage-io` 是标准运行的唯一阶段入口。完整机器状态留在受管目录，Agent 每次只读取 `next` 返回的当前 `contract_file`、`packet_file`，并在宿主支持时用 `result_schema_file` 约束结果。

`prepare-run --agent-view` 只向宿主返回 `run_dir`、公共周期、身份/模板调用、元数据波次和最终交付动作；完整 `run-plan.json` 与紧凑 `execution-graph.json` 留在受管目录，不进入 Agent 上下文。新静态图固定分类、批量提取和最多一次综合三个语义节点，以及抓取、编译、确定性回填、渲染和完成等非语义节点。非空双账本仍恰好执行一次模型综合；只有 `work=[]` 且 `uncertain=[]` 时才由确定性层生成规范报告模型并跳过综合。未声明该能力的旧静态图和没有图描述符的兼容运行仍保持一次综合。运行时按 `stage_runtime_order` 选择下一节点并校验图摘要；图不能增加语义节点、把综合改成多次或取消批量提取。

```bash
python3 scripts/stage-io.py next --run-dir <run-dir>
python3 scripts/stage-io.py commit \
  --run-dir <run-dir> \
  --result-file <stage-result.json> \
  --package-id <next 返回的 package_id> \
  [--usage-file <host-usage.json>]
```

`package_id`、图摘要、输入摘要、账本指纹、全局 ID、来源和文件路径都由私有回执管理，不写入模型包或模型结果。旧宿主仍可在结果中带回 `schema_version`、`package_id` 和 `input_digest`，但新流程应使用命令参数。

`next` 为 `fetch`、`extract`、`synthesize` 返回唯一阶段契约及其摘要。`context_mode=fresh` 时，宿主应使用相同模型开启一次无历史调用，只传当前契约和包；不支持隔离上下文时继续当前调用，不得删除包内请求、主体或周期约束。

空双账本旁路仍经过同一个定稿与完整报告校验流程；完成后接口写入私有摘要回执，绑定执行图、账本、运行计划、提取审计、可选身份/模板/请求上下文、规范报告模型和最终 `report.md`。回执不存在、陈旧或任一摘要不匹配时均不承认旁路，计量层继续要求真实 `synthesize` 用量。

提交后接口检查逐项覆盖、字段白名单、短引用、受保护文件摘要和全账本覆盖。常见单项错误会返回 `repair_packet` 与 `repair_schema`；修复结果只提交允许路径的 `patches`，接口与原结果合并后重新执行完整校验。编译层错误继续使用 `repair-queue.json`。

推荐由宿主把真实用量写入受管目录内的独立 JSON，再用 `--usage-file` 提交：

```json
{"schema_version":1,"provider":"openai","model":"同一实际模型标识","input_tokens":1000,"output_tokens":200,"total_tokens":1200}
```

可选记录 `cached_input_tokens` 和 `reasoning_tokens`。接口拒绝负数、不一致总量、未知字段、外部路径，以及结果体与用量文件同时报数。旧宿主仍可在阶段结果中附带不含模型身份的 `usage`，但它只用于兼容计量，不能作为“同模型 A/B”的证据。

分类节点不经过 `stage-io commit`。宿主用相同格式的用量文件执行：

```bash
python3 scripts/record-node-usage.py \
  --run-dir <run-dir> \
  --node-id classify \
  --invocation-id classify-1 \
  --usage-file <host-usage.json> \
  --elapsed-ms <真实耗时> \
  --input-file <candidates.json> \
  --result-file <fetch-queue.json>
```

接口把包体、结果体、耗时及真实用量写入私有 `stage-metrics.json`；不得估算 Token，也不得把该文件送入后续模型。失败调用在能确认节点和用量时也单独计入，增量修复不会抹掉已经消耗的 Token。

节点用量用于归因，不能替代宿主对整个任务的聚合账单。完成报告后、清理运行目录前，再记录同一宿主返回的全程真实用量：

```bash
python3 scripts/record-run-usage.py \
  --run-dir <run-dir> \
  --usage-file <aggregate-host-usage.json> \
  --elapsed-ms <任务全程真实耗时>
```

聚合用量应覆盖根 Agent 的流程控制、语义节点和失败重试。比较器用它计算总 Token 与总耗时，避免把“节点上下文变小”误报成“整个任务更省”。

## 同数据、同模型 A/B

对基线和候选各完成一次真实运行，确保所有语义节点及任务全程都由宿主上报用量，然后执行：

```bash
python3 scripts/compare-runs.py \
  --baseline-run <baseline-run-dir> \
  --candidate-run <candidate-run-dir> \
  --output <comparison.json>
```

比较器用周期、请求、身份、模板、候选元数据和完整规范化正文生成数据指纹，并逐节点核对 `provider` 与 `model`。通常两侧每个实际语义节点的模型集合必须相同；唯一例外是一侧以有效私有回执跳过空账本综合，且两侧最终 `report.md` 字节摘要完全一致。其他节点缺失、回执无效、报告不同，或数据指纹、模型集合、完成状态、工作/待复核计数、分类结果计数、来源集合不一致时，结论均为 `inconclusive`，不得把差异宣传为无损优化。

失败时不得修改包、私有回执、队列、账本、Schema 或确定性字段来绕过校验。最终交付只使用 `stage=complete` 返回的 `report_file`。
