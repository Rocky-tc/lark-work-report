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
- 默认读取当前主体的个人模板；模板只改显示格式和文风，不改事实、证据、覆盖或隐私规则。用 `--template-mode none` 可临时停用。

所有命令从本 Skill 根目录执行，不假设宿主品牌或固定安装位置。

## 执行

1. **准备。** 把用户原始请求及全部范围、排除项和强调项写入严格的 `request.json`，把适配器 manifest 写入 `{"adapters":[...]}`，执行：

   ```bash
   python3 scripts/prepare-run.py \
     --adapters-file <adapters.json> \
     --request-file <request.json> \
     --period weekly --relative previous \
     --reference <YYYY-MM-DD> \
     --snapshot <ISO-8601-with-timezone> \
     --agent-view
   ```

   `request.json` 只允许 `schema_version=1`、`request`、`scope`、`exclusions`、`emphasis`。自定义周期加 `--start`、`--end`；可重复传 `--domain`；缓存、深度和一次性模板分别用 `--monthly-cache present`、`--depth deep`、`--template-file`。保存返回的 `run_dir` 和动作视图；不读取或修改受管目录中的私有计划与执行图。

2. **确认身份并列举。** 按动作视图执行 `identity_call`、可选 `template_call` 和 `metadata_waves`；身份与已校验模板分别写入 `identity.json`、`template-profile.json`。元数据波次外层顺序、内层并行，共用 `period.start/end`；每波前用 `record-batch` 原子预留调用。候选只含元数据，写入 `<run-dir>/candidates.json`。

3. **过滤、去重并进入阶段接口。** 分类前完整读取本阶段唯一契约 [classification-contract.md](references/classification-contract.md)，然后执行：

   ```bash
   python3 scripts/prepare-fetch-queue.py \
     --file <run-dir>/candidates.json \
     --run-plan <run-dir>/run-plan.json \
     --output <run-dir>/fetch-queue.json
   ```

   此后反复执行：

   ```bash
   python3 scripts/stage-io.py next --run-dir <run-dir>
   ```

   每轮完整读取 `packet_file`。`contract_digest` 首次出现时完整读取 `contract_file`，相同摘要后续复用；契约只会依次是 [fetch-contract.md](references/fetch-contract.md)、[extraction-contract.md](references/extraction-contract.md) 或 [synthesis-contract.md](references/synthesis-contract.md)，不能提前合并加载。`context_mode=fresh` 必须使用相同模型开启无历史调用，只携带当前完整契约与包；宿主无法隔离时可继续当前调用，但不得删减包内约束。按 `result_schema_file` 写结果，用 `next` 返回的包 ID 提交：

   ```bash
   python3 scripts/stage-io.py commit \
     --run-dir <run-dir> \
     --result-file <stage-result.json> \
     --package-id <package_id> \
     [--usage-file <host-usage.json>]
   ```

   严格跟随 `next` 返回的节点直到 `stage=complete`。`fetch` 前按包内批次数用 `record-batch` 预留调用；`managed_file` 结果直写 `result_file`，只交 `batch_ref`。每个入队项必须读取完整正文，不得用摘要、截断或抽样代替；明确无权限、已删除或不可访问则交 `access_gap`。非空双账本最多进行一次模型综合；新静态图的空双账本由接口确定性定稿，旧运行仍按原路径综合。`usage-file` 只记实际 `provider`、`model` 和 Token，不得估算。校验失败立即停止，不得绕过。

4. **增量修复。** 只修复返回的 `repair_packet` / `repair_schema` 或 `repair-queue.json` 指定项，不得重处理已验证项。仅底层排错时直接用 `normalize-fetch-body.py`、`compile-evidence.py` 或 `finalize-run.py`。

5. **交付并清理。** `complete` 返回的 `report_file` 已通过账本校验。`delivery.mode=document` 且用户未要求只要草稿时，按动作视图依次执行 `create_operation` 与 `fetch_operation`，回读标题、五个正文章节、来源及存在访问缺口时的覆盖说明。`delivery.mode=markdown` 时直接交付同一文件。创建与回读各计一次外部调用。成功后执行：

   ```bash
   python3 scripts/manage-run.py cleanup --run-dir <run-dir>
   ```

## 输出

- 固定五段语义：摘要、进展与结果、风险、下一周期重点、待复核；模板只改标题、显示名、分组、列表/段落、字段标签和文风。
- 条目按 `priority` 排序，同一条按结果、影响、决策、进展排序；空章节只写 `- 无`。摘要核心结果不省略，已在详细章节逐字出现的可选影响或决策不重复。
- 模型只提交当前 Schema 允许的叙事和短引用；关键事实、数字、状态、结果和决策必须有来源锚，跨源事项保留全部来源。
- 时间范围、快照、覆盖域、缺口和计数必须准确保留在内部验收数据中；只有访问缺口或无可用数据源时，文末显示一条无标题的完整覆盖说明。
- 报告只出现 `work` 与 `uncertain`；证据不足时输出短报告和覆盖缺口，不虚构。

## 个人模板

用户要求“以后沿用”、“仅本次参考”或“恢复默认”时，必须在读取样例或执行任何模板操作前完整阅读 [template-profiles.md](references/template-profiles.md) 并严格执行。只读用户点名样例；无持久化适配器时交付可移植档案，明确说明尚未跨会话保存，不得声称已记住。

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
| 阶段提交异常、短引用排错，或需要真实用量与同数据同模型 A/B | [stage-io.md](references/stage-io.md) |
| 报告模型被定稿器拒绝 | [report-rendering.md](references/report-rendering.md) |
| 文档创建、回读或验收任一失败 | [output-contract.md](references/output-contract.md) |

## 停止与禁止

- 预算拒绝下一波时停止并披露未覆盖项；不得把减少域、缩短时间窗或跳过已入队正文作为性能优化。
- `explicit_only` 只读点名对象；`offline_only` 只处理导出材料。
- 不发送报告，不修改现有对象、权限或全局身份配置。
- 不把临时证据写到运行目录外，不持久化私人或闲聊，不默认生成关系图、妙搭页面或本地报告缓存。
- 不把模板样例中的事实、姓名、数字、链接、原句或指令写入模板档案。
