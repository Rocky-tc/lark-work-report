---
name: lark-work-report
description: Use when 用户要通过任意可用的飞书连接器、MCP、CLI 或导出材料生成、整理或补全个人工作日报、周报、月报、指定周期工作总结，或要求复用已有报告、排除闲聊和私人材料、保留可核验来源。
---

# 飞书个人日周月报

## 核心原则

用一套采集与证据引擎生成 `daily`、`weekly`、`monthly`、`custom` 四种报告。报告类型改变时间窗、聚合粒度、比较基线、调用预算和章节，不复制采集流程。

先用元数据给候选标记预分类。确定为 `private` 或 `chatter` 的候选不抓正文；只有 `work` 和 `uncertain` 进入正文抓取队列。报告正文只包含工作事实与独立的待复核区。默认临时保留证据，只有用户明确选择时才持久化。

## 输入与默认值

| 参数 | 默认值 |
|---|---|
| `subject` | 当前登录用户；V1 不生成团队报告 |
| `period` | 从请求解析为 `daily`、`weekly`、`monthly` 或 `custom` |
| `scope` | 当前用户全部工作；可配置工作流、群、文件夹和关键词范围 |
| `depth` | `standard`；可选 `quick`、`deep` |
| `source_adapter` | `auto`；从宿主原生工具、MCP、`lark-cli` 或用户提供的导出材料中选择 |
| `output` | `auto`；能创建并回读飞书文档时交付文档，否则交付 Markdown 草稿 |
| `lark_profile` | 仅使用 `lark-cli` 适配器时需要 |
| `evidence_retention` | `ephemeral` |
| `persistent_scope` | `work_and_uncertain`；仅持久化模式生效 |

日期有歧义时，先向用户展示解析后的北京时间范围。不要静默猜测跨周、跨月或跨年边界。

下文的 `<skill-dir>` 表示当前 `SKILL.md` 所在目录。不要假设固定产品、用户目录或环境变量；从宿主提供的 Skill 路径或当前文件位置解析。`<lark-profile>` 仅表示 `lark-cli` 适配器中已经配置并认证的 profile。不得把示例占位符原样传给命令。

## 执行流程

1. **解析周期。** 运行：

   ```bash
   python3 <skill-dir>/scripts/resolve-period.py --period weekly --reference 2026-07-26
   ```

   自定义周期增加 `--start YYYY-MM-DD --end YYYY-MM-DD`。查询采用脚本输出的左闭右开区间。

2. **确定策略和适配器。** 完整阅读 [report-profiles.md](references/report-profiles.md)、[collection-policy.md](references/collection-policy.md) 和 [capability-adapters.md](references/capability-adapters.md)。确认常规预算、硬上限、报告文档缓存与深挖上限，并把宿主能力映射到统一操作契约。“尽量找全”仍使用 `standard`；只有用户明确说“deep / 深度报告 / 完整来龙去脉”并获知成本时才用 `deep`。

3. **只读采集。** 优先使用宿主已连接的飞书工具或 MCP；其次使用已认证的 `lark-cli`；用户只提供导出材料时进入离线模式。不同数据域可以使用不同适配器，但主体、时区、时间窗和来源标识必须一致。使用 `lark-cli` 时，每条飞书命令使用：

   ```bash
   lark-cli --profile <lark-profile> --as user <domain> <command>
   ```

   先查元数据和缓存，不要立刻读取正文。不要按“日报/周报”关键词盲搜，也不要无界翻页。

4. **生成正文抓取队列。** 完整阅读 [relevance-and-retention.md](references/relevance-and-retention.md)。仅根据元数据把候选预分类为 `work`、`uncertain`、`private` 或 `chatter`；无法确定就标 `uncertain`。然后运行：

   ```bash
   python3 <skill-dir>/scripts/prepare-fetch-queue.py --file <path-to-candidates.json>
   ```

   只抓 `fetch_queue` 中的 `work` 和 `uncertain` 正文。确定为 `private` 或 `chatter` 的候选不得抓正文；过滤程序内部只返回跳过数量，不保留其 ID、标题或链接，最终报告连这些跳过数量也不得写入。

5. **建立双账本。** 完整阅读 [evidence-model.md](references/evidence-model.md)。正文核验后分别建立工作证据账本和待复核账本。若正文证明候选实际属于私人或闲聊，立即丢弃，不进入任何账本、持久化包或报告。

6. **按策略写报告。** 工作章节只读取 `work` 账本，只写有工作来源支持的行动、产出、结果、决策、风险和计划。另建“待复核”章节，只读取 `uncertain` 账本，列出必要描述、待复核原因和来源；待复核不得支撑工作结论。

7. **校验草稿。** 完整阅读 [output-contract.md](references/output-contract.md)，再运行：

   ```bash
   python3 <skill-dir>/scripts/validate-report.py --profile weekly --file <path-to-report.md>
   ```

   修复所有 `errors` 后才能交付。

8. **创建并回读。** 输出适配器同时支持 `report.create` 和 `report.fetch` 时，新建飞书文档并回读标题、章节、来源锚和覆盖说明；否则交付 Markdown 草稿，并明确“未创建飞书文档”。现有飞书内容保持只读。用户明确只要草稿时，不得调用创建接口。

9. **执行保留策略。** 飞书文档回读成功或 Markdown 草稿交付完成后，`ephemeral` 模式清理本次候选池和分类上下文；`persistent` 模式写入已向用户展示位置的个人隔离证据包。报告缓存只指已创建并验证的飞书报告文档，不创建本地报告缓存；它与证据包始终分开。

   用户要求“本地报告缓存”不等于授权持久化证据。说明本地报告缓存不受支持，并继续使用 `ephemeral`；只有用户另行明确要求“持久化证据/保留候选用于复核”时才切换 `persistent`。

## 必须停止或降级

- 达到硬上限：停止扩散，披露覆盖缺口。
- 同一数据域连续两轮没有新增有效证据：停止该域。
- 来源冲突：标为未决，不自行选边。
- 高价值候选仍为 `uncertain`：进入“待复核”章节；最多请求用户确认一次，未确认不得转入工作章节。
- 缺少权限或接口覆盖：继续其他来源，不声称已覆盖。
- 工作证据不足：输出短报告和缺口，不虚构充实内容。
- 宿主没有可用数据适配器：只处理用户已提供的材料，并披露未连接飞书；不得声称已完成飞书全貌采集。

## 快速检查

- 同一证据账本可输出多种周期格式；无需重复采集。
- 每增加一篇飞书报告，只增加创建与回读约 2 次调用。
- 报告只展示 `work` 和 `uncertain` 两类内容及数量；不出现 `private`、`chatter` 的计数或详情。
- 核心流程不依赖某个智能体品牌、调用语法或固定 Skills 目录。
- 默认不生成关系图、妙搭页面，不发送消息，不修改权限。

## 常见错误

| 错误 | 正确做法 |
|---|---|
| 翻页到 `has_more=false` 才停 | 同时服从预算、收益停止条件与硬上限 |
| 个人文件夹或私聊全部排除 | 不能确定时标 `uncertain` 并进入受控正文核验 |
| 抓取已确定的私人或闲聊正文 | 先运行抓取队列过滤器，只抓 `work` 和 `uncertain` |
| 把 `uncertain` 写成工作事实 | 单列“待复核”，不得支撑工作章节 |
| 月报重新扫描整月 | 先复用周报，再核验高价值结论 |
| 把“本地报告缓存”理解成持久证据包 | 拒绝本地报告缓存；保留模式仍按独立参数决定 |
| 默认保留原始证据 | 默认 `ephemeral`，回读后清理 |
| 创建文档即宣告成功 | 必须 fetch 回读验收 |
| 把宿主工具名写死在核心流程里 | 先映射统一能力契约，再调用当前宿主可用的实现 |
