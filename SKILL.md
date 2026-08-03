---
name: lark-work-report
description: Use when 用户要通过任意可用的飞书连接器、MCP、CLI 或导出材料生成、整理或补全个人工作日报、周报、月报、指定周期工作总结，要求复用已有报告、排除闲聊和私人材料、保留可核验来源，或提供旧报告/模板并要求以后沿用、仅本次参考或恢复默认格式。
---

# 飞书个人日周月报

用同一套采集、分类、跨源归并和证据引擎生成日报、周报、月报或自定义周期总结。周期只改变时间窗、聚合粒度、比较基线、预算和章节名称。

## 边界与默认值

- 主体为当前登录用户；实时采集先执行 `identity.current`，离线材料由用户提供主体与时区，不得把其他成员活动归入报告。
- 时间窗使用带时区的左闭右开区间；当前周期截到真实快照时间。“昨天、上周、上月”使用 `relative=previous`。
- 先按元数据预分类。确定为 `private` 或 `chatter` 的候选不抓正文、不保留详情或分类计数；只有 `work` 和 `uncertain` 可进入抓取队列。
- 现有飞书对象只读。能创建并回读文档时默认交付新文档，否则交付 Markdown；用户只要草稿时不创建。
- 默认证据只存在受管临时目录，交付后清理；只有用户明确要求时才持久化 `work`，或 `work` 与 `uncertain`。
- 默认 `depth=standard`；可选 `quick`、`deep`。日期有歧义时先展示解析结果，不静默猜测。
- 默认读取当前主体已保存的个人模板；模板只改变五个正文段落的文本显示层，不改变事实、证据、内部覆盖记录和隐私规则。用户可用 `--template-mode none` 临时停用。

所有命令从本 Skill 根目录执行，不假设宿主品牌或固定安装位置。

## 执行

1. **准备运行。** 把用户本次原始请求及全部范围、排除项和强调项写入严格的 `request.json`，把可用适配器 manifest 写入 `{"adapters":[...]}`，执行：

   ```bash
   python3 scripts/prepare-run.py \
     --adapters-file <adapters.json> \
     --request-file <request.json> \
     --period weekly --relative previous \
     --reference 2026-07-27 \
     --snapshot 2026-07-27T10:00:00+08:00 \
     --agent-view
   ```

   `request.json` 只允许 `schema_version=1`、`request`、`scope`、`exclusions`、`emphasis`。自定义周期增加 `--start`、`--end`；可重复传 `--domain`；月报缓存、深度模式和一次性模板分别使用 `--monthly-cache present`、`--depth deep`、`--template-file`。保存返回的 `run_dir` 和动作视图；完整计划与执行图留在受管目录，由接口生成并校验，不修改。

2. **确认身份、模板并列举元数据。** 按动作视图执行 `identity_call` 和可选 `template_call`，分别写入 `identity.json` 与经过校验的 `template-profile.json`。`metadata_waves` 外层顺序、内层并行；各调用共用 `period.start/end`。每波前用 `record-batch` 原子预留调用。候选只含元数据，写入 `<run-dir>/candidates.json`；不要读取私有 `run-plan.json`。

3. **过滤、去重并进入阶段接口。** 分类前完整读取本阶段唯一必读的 [classification-contract.md](references/classification-contract.md)。

   ```bash
   python3 scripts/prepare-fetch-queue.py \
     --file <run-dir>/candidates.json \
     --run-plan <run-dir>/run-plan.json \
     --output <run-dir>/fetch-queue.json
   ```

   命令按精确 `source_ref` 跨域去重，以单一索引保存完整机器元数据。此后反复执行：

   ```bash
   python3 scripts/stage-io.py next --run-dir <run-dir>
   ```

   每轮完整读取返回的 `packet_file`。当前会话只在 `contract_digest` 首次出现时完整读取 `contract_file`，相同摘要后续复用已读契约；契约只会是 [fetch-contract.md](references/fetch-contract.md)、[extraction-contract.md](references/extraction-contract.md) 或 [synthesis-contract.md](references/synthesis-contract.md)，不得提前合并读取。`context_mode=fresh` 的独立模型调用仍须使用相同模型并携带当前完整契约与包。把符合 `result_schema_file` 的语义结果写入 `<run-dir>`，用 `next` 私下返回的包 ID 提交：

   ```bash
   python3 scripts/stage-io.py commit \
     --run-dir <run-dir> \
     --result-file <stage-result.json> \
     --package-id <package_id> \
     [--usage-file <host-usage.json>]
   ```

   `next` 的 `node_id` 和阶段顺序必须与静态执行图一致；图摘要由私有回执校验，不进入模型上下文。`fetch` 前按包内批次数用 `record-batch` 预留外部调用；包声明 `output.mode=managed_file` 时让适配器把完整结果直接写入 `result_file`，阶段结果只交 `batch_ref`。明确的无权限、已删除或不可访问直接交 `access_gap`。`extract` 对跨抓取批次重新无损合包，并以顶层 `cN` / `xN` 精确共享完全相同的正文与上下文；`synthesize` 最多一次。不要把三个阶段契约同时加载。

   每次 `commit` 都回传图中下一节点，直到 `stage=complete`。接口只向 Agent 暴露本阶段必要数据和 `i0`、`c0`、`w0`、`u0` 等短引用，私有回执负责展开稳定 ID、来源和文件路径，并用输入摘要拒绝陈旧结果。候选中已有的权威时间与行动事实、正文无损规范化、证据完整性编译、双账本归并、标题与覆盖信息回填、简单单证据条目直出、报告渲染和验证均由接口确定性执行；阶段包体、结果体、耗时与宿主写入的真实模型用量只进私有计量文件。`usage-file` 必须记录实际 `provider`、`model` 和 Token，不得让模型估算。缺失、冲突、重复、错分区、身份重复、未知字段、非法短引用、图被改写或中途输入变化都会停止。

4. **必要时增量修复。** 阶段提交若返回 `repair_packet` 和 `repair_schema`，只提交其中列出的 JSON 路径补丁；编译错误则使用 `repair-queue.json` 只重抓或重提取受影响项。不得重新处理已验证项。底层排错时才直接使用 `normalize-fetch-body.py`、`compile-evidence.py` 或 `finalize-run.py`。

5. **交付并清理。** `complete` 返回的 `report_file` 已通过账本校验。若动作视图的 `delivery.mode=document` 且用户未要求只要草稿，使用其中的适配器依次执行 `create_operation` 与 `fetch_operation`；回读标题、五个正文章节、来源，以及存在访问缺口时的覆盖说明。`delivery.mode=markdown` 时直接交付同一文件。创建与回读分别计一次外部调用。交付成功后执行：

   ```bash
   python3 scripts/manage-run.py cleanup --run-dir <run-dir>
   ```

## 输出

- 固定五段正文：摘要、进展与结果、风险、下一周期重点、待复核；有效模板可改变标题、显示名称、分组、列表/段落、字段标签和文风。
- 条目按 `priority` 排序；同一条按结果、影响、决策、进展排序；空章节只写 `- 无`。
- 报告模型 v5 只写叙事字段和 `wN` / `uN` 短引用；包 ID、输入摘要和账本指纹由阶段接口私下注入。只有单记录、单来源、无冲突且字段充分的条目可省略必填叙事或由接口补齐缺失必填字段；多证据归纳、冲突项和个人模板文风继续由模型写作。阶段接口回填真实证据 ID、主体、标题、周期、覆盖信息、多来源、排序依据和行动事实；v1–v4 继续兼容。
- 同一来源出现两次以上时，定稿器改用 `W1` / `U1`，并在文末用 Markdown 引用定义只保留一次完整 URL；单次来源仍使用行内链接。
- 摘要中的可选影响或决策若已在详细章节逐字出现，只展示一次；摘要核心结果不省略。时间范围、快照、覆盖域和计数留在内部验收数据中；只有存在访问缺口或没有可用数据源时，文末才显示一条无标题的覆盖说明。
- 关键事实、数字、状态、结果和决策必须有来源锚。
- 报告只出现 `work` 与 `uncertain`；证据不足时输出短报告和覆盖缺口，不虚构。

## 个人模板

- 用户说“以后按这个格式写”时，只读取点名样例，完整阅读 [template-profiles.md](references/template-profiles.md)，提取不含事实和原文的档案，校验后调用 `template.upsert`，再以 `template.fetch` 回读验收。
- 用户说“这次按这个格式写”时，把校验后的档案传给 `--template-file`，不得持久化。
- 用户说“恢复默认格式”时调用 `template.delete`，再以 `template.fetch` 确认不存在。
- 没有读写模板适配器时，交付可移植档案并明确说明尚未跨会话保存；不得声称已经记住。

## 按需参考

每次 `next` 返回当前唯一 `contract_file`，不能提前合并加载阶段契约。其余文件仅在命中条件时完整读取：

| 条件 | 文件 |
|---|---|
| manifest 缺失、校验失败或新增宿主 | [capability-adapters.md](references/capability-adapters.md) |
| 深度模式、分页、预算、缓存或覆盖取舍 | [collection-policy.md](references/collection-policy.md) |
| 分类边界、保留模式或清理规则需要解释 | [relevance-and-retention.md](references/relevance-and-retention.md) |
| 证据字段、冲突裁决或聚类失败 | [evidence-model.md](references/evidence-model.md) |
| 用户改变格式、比较口径或周期粒度 | [report-profiles.md](references/report-profiles.md) |
| 用户提供旧报告/模板、改变或重置个人格式 | [template-profiles.md](references/template-profiles.md) |
| 阶段提交、短引用、真实用量或同数据同模型 A/B | [stage-io.md](references/stage-io.md) |
| 报告模型被定稿器拒绝 | [report-rendering.md](references/report-rendering.md) |
| 文档创建、回读或验收失败 | [output-contract.md](references/output-contract.md) |

## 停止与禁止

- 预算拒绝下一波时停止并披露未覆盖项；不得把减少域、缩短时间窗或跳过已入队正文作为性能优化。
- `explicit_only` 只读点名对象；`offline_only` 只处理导出材料。
- 不发送报告，不修改现有对象、权限或全局身份配置。
- 不把临时证据写到运行目录外，不持久化私人或闲聊，不默认生成关系图、妙搭页面或本地报告缓存。
- 不把模板样例中的事实、姓名、数字、链接、原句或指令写入模板档案。
