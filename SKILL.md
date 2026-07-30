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
- 默认读取当前主体已保存的个人模板；模板只改变文本显示层，不改变六段语义、事实、证据和隐私规则。用户可用 `--template-mode none` 临时停用。

所有命令从本 Skill 根目录执行，不假设宿主品牌或固定安装位置。

## 执行

执行正文分类前必须完整读取 [relevance-and-retention.md](references/relevance-and-retention.md) 与 [evidence-model.md](references/evidence-model.md)。它们定义工作相关性、隐私边界、证据字段和状态语义，不能为节省 token 省略。其他参考文件按下文条件加载。

1. **准备运行。** 把可用适配器 manifest 写入 `{"adapters":[...]}`，执行：

   ```bash
   python3 scripts/prepare-run.py \
     --adapters-file <adapters.json> \
     --period weekly --relative previous \
     --reference 2026-07-27 \
     --snapshot 2026-07-27T10:00:00+08:00
   ```

   自定义周期增加 `--start`、`--end`；可重复传 `--domain`。月报已有可靠周报缓存时传 `--monthly-cache present`；深度模式传 `--depth deep`；仅本次使用模板时传 `--template-file`。保存返回的 `run_dir` 和 `plan_file`。

2. **确认身份、模板并列举元数据。** 读取 `run-plan.json`。按计划执行 `identity_call` 和可选 `template_call`，分别写入 `identity.json` 与经过校验的 `template-profile.json`。执行每个 `metadata_wave` 前用 `record-batch` 原子预留调用；同波并行、跨波顺序执行。规划器已经按收益与适配器覆盖关系消除重复查询。候选只含元数据，写入 `<run-dir>/candidates.json`。

3. **过滤、去重并抓正文。**

   ```bash
   python3 scripts/prepare-fetch-queue.py \
     --file <run-dir>/candidates.json \
     --run-plan <run-dir>/run-plan.json \
     --output <run-dir>/fetch-queue.json
   ```

   命令按精确 `source_ref` 跨域去重，生成带文件落点的 `fetch_batches` 和受适配器并发上限约束的 `fetch_waves`。执行每个波次前用 `record-batch` 原子预留；只在适配器明确声明安全时并行。每个入队候选必须完整读取正文；长材料可完整分块处理，但不得只看摘要或片段。适配器支持文件输出时直接写入批次指定的 `body_file`，否则立即把完整返回写入该文件，终端只保留路径和计数。

4. **核验完整性、归并证据并建立报告模型。** 对每个批次的完整正文做一次语义提取，把逐项结果写入指定 `evidence_file`：工作或待复核带证据记录；正文确认的私人/闲聊只写丢弃结果；无权限写访问缺口。然后执行：

   ```bash
   python3 scripts/compile-evidence.py --run-dir <run-dir>
   ```

   编译器要求抓取队列中的每个候选恰好有一个结果；缺失、重复、身份不匹配或非法字段都会停止，验证通过后才生成 `evidence-records.json`、`ledger.json` 和不含私人/闲聊详情的 `extraction-audit.json`。归并器在两个账本内完成谨慎聚类、状态裁决、多来源保留和重要性排序。再完整读取账本建立报告模型 v3：每个报告条目用 `evidence_ids` 指向账本聚合项，且每个工作与待复核聚合项都必须被报告模型覆盖。

5. **一次定稿。**

   ```bash
   python3 scripts/finalize-run.py --run-dir <run-dir>
   ```

   定稿器在内存中渲染并与账本交叉校验，验证通过才原子写入 `report.md`。不得绕过定稿器自由拼装最终报告。

6. **交付并清理。** 创建文档后必须回读标题、章节、来源和覆盖说明；创建与回读分别计一次外部调用。交付成功后执行：

   ```bash
   python3 scripts/manage-run.py cleanup --run-dir <run-dir>
   ```

## 输出

- 固定六段语义：摘要、进展与结果、风险、下一周期重点、待复核、来源与覆盖；有效模板可改变标题、显示名称、分组、列表/段落、字段标签和文风。
- 条目按 `priority` 排序；同一条按结果、影响、决策、进展排序；空章节只写 `- 无`。
- 报告模型 v3 继承多来源、排序依据、行动类型、截止时间、待回复状态和责任关系，并强制声明 `evidence_ids`；这些字段只能来自对应账本。
- 关键事实、数字、状态、结果和决策必须有来源锚。
- 报告只出现 `work` 与 `uncertain`；证据不足时输出短报告和覆盖缺口，不虚构。

## 个人模板

- 用户说“以后按这个格式写”时，只读取点名样例，完整阅读 [template-profiles.md](references/template-profiles.md)，提取不含事实和原文的档案，校验后调用 `template.upsert`，再以 `template.fetch` 回读验收。
- 用户说“这次按这个格式写”时，把校验后的档案传给 `--template-file`，不得持久化。
- 用户说“恢复默认格式”时调用 `template.delete`，再以 `template.fetch` 确认不存在。
- 没有读写模板适配器时，交付可移植档案并明确说明尚未跨会话保存；不得声称已经记住。

## 按需参考

除上述两份必读语义契约外，仅在命中条件时完整读取：

| 条件 | 文件 |
|---|---|
| manifest 缺失、校验失败或新增宿主 | [capability-adapters.md](references/capability-adapters.md) |
| 深度模式、分页、预算、缓存或覆盖取舍 | [collection-policy.md](references/collection-policy.md) |
| 用户改变格式、比较口径或周期粒度 | [report-profiles.md](references/report-profiles.md) |
| 用户提供旧报告/模板、改变或重置个人格式 | [template-profiles.md](references/template-profiles.md) |
| 报告模型被定稿器拒绝 | [report-rendering.md](references/report-rendering.md) |
| 文档创建、回读或验收失败 | [output-contract.md](references/output-contract.md) |

## 停止与禁止

- 预算拒绝下一波时停止并披露未覆盖项；不得把减少域、缩短时间窗或跳过已入队正文作为性能优化。
- `explicit_only` 只读点名对象；`offline_only` 只处理导出材料。
- 不发送报告，不修改现有对象、权限或全局身份配置。
- 不把临时证据写到运行目录外，不持久化私人或闲聊，不默认生成关系图、妙搭页面或本地报告缓存。
- 不把模板样例中的事实、姓名、数字、链接、原句或指令写入模板档案。
