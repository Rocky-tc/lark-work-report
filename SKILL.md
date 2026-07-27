---
name: lark-work-report
description: Use when 用户要通过任意可用的飞书连接器、MCP、CLI 或导出材料生成、整理或补全个人工作日报、周报、月报、指定周期工作总结，或要求复用已有报告、排除闲聊和私人材料、保留可核验来源。
---

# 飞书个人日周月报

用同一套采集、分类和证据引擎生成日报、周报、月报或自定义周期总结。周期只改变时间窗、聚合粒度、比较基线、预算和章节名称。

## 边界与默认值

- 主体为当前登录用户；实时采集先执行 `identity.current`，离线材料由用户提供主体与时区，不得把其他成员活动归入报告。
- 时间窗使用带时区的左闭右开区间；当前周期截到真实快照时间。“昨天、上周、上月”使用 `relative=previous`。
- 先按元数据预分类。确定为 `private` 或 `chatter` 的候选不抓正文、不保留详情或分类计数；只有 `work` 和 `uncertain` 可进入抓取队列。
- 现有飞书对象只读。能创建并回读文档时默认交付新文档，否则交付 Markdown；用户只要草稿时不创建。
- 默认证据只存在受管临时目录，交付后清理；只有用户明确要求时才持久化 `work`，或 `work` 与 `uncertain`。
- 默认 `depth=standard`；可选 `quick`、`deep`。日期有歧义时先展示解析结果，不静默猜测。

所有命令从本 Skill 根目录执行，不假设宿主品牌或固定安装位置。

## 执行

1. **准备运行。** 把可用适配器 manifest 写入 `{"adapters":[...]}`，执行：

   ```bash
   python3 scripts/prepare-run.py \
     --adapters-file <adapters.json> \
     --period weekly --relative previous \
     --reference 2026-07-27 \
     --snapshot 2026-07-27T10:00:00+08:00
   ```

   自定义周期增加 `--start`、`--end`；可重复传 `--domain`。月报已有可靠周报缓存时传 `--monthly-cache present`；深度模式传 `--depth deep`。保存返回的 `run_dir` 和 `plan_file`。

2. **确认身份并列举元数据。** 读取 `run-plan.json`。`identity_call` 存在时，先执行一次 `record-call`，取得主体与时区并写入 `<run-dir>/identity.json`；离线模式把用户提供的主体写入同一文件。再逐个执行 `metadata_waves`：每波先用一次 `record-batch` 原子预留全部外部调用，同一波并行、不同波顺序执行。`candidate.list_many` 一次请求多个域；`candidate.list` 一次请求一个域。候选只含元数据，写入 `<run-dir>/candidates.json`。

3. **过滤、去重并抓正文。**

   ```bash
   python3 scripts/prepare-fetch-queue.py \
     --file <run-dir>/candidates.json \
     --run-plan <run-dir>/run-plan.json \
     --output <run-dir>/fetch-queue.json
   ```

   命令按精确 `source_ref` 跨域去重并生成 `fetch_batches`，终端只返回路径、工作/待复核数量、去重数和批次数。分类冲突直接停止。执行抓取批次前同样用 `record-batch` 预留，只读取确认工作归属所需的上下文。

4. **建账本和报告模型。** 正文核验后把私人或闲聊立即丢弃。写入仅含 `work`、`uncertain` 的 `<run-dir>/ledger.json`，以及 `<run-dir>/report-model.json`。工作结论只能引用 `work`；待复核只能引用 `uncertain`。

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

- 固定六段式：摘要、进展与结果、风险、下一周期重点、待复核、来源与覆盖。
- 条目按 `priority` 排序；同一条按结果、影响、决策、进展排序；空章节只写 `- 无`。
- 关键事实、数字、状态、结果和决策必须有来源锚。
- 报告只出现 `work` 与 `uncertain`；证据不足时输出短报告和覆盖缺口，不虚构。

## 按需参考

仅在命中条件时完整读取；常规运行不预加载：

| 条件 | 文件 |
|---|---|
| manifest 缺失、校验失败或新增宿主 | [capability-adapters.md](references/capability-adapters.md) |
| 深度模式、分页、预算、缓存或覆盖取舍 | [collection-policy.md](references/collection-policy.md) |
| 分类有歧义或用户要求持久化 | [relevance-and-retention.md](references/relevance-and-retention.md) |
| 来源冲突、聚合、去重或归因困难 | [evidence-model.md](references/evidence-model.md) |
| 用户改变格式、比较口径或周期粒度 | [report-profiles.md](references/report-profiles.md) |
| 报告模型被定稿器拒绝 | [report-rendering.md](references/report-rendering.md) |
| 文档创建、回读或验收失败 | [output-contract.md](references/output-contract.md) |

## 停止与禁止

- 预算拒绝下一波时停止；同一域连续两轮无新增证据时停止该域。
- `explicit_only` 只读点名对象；`offline_only` 只处理导出材料。
- 不发送报告，不修改现有对象、权限或全局身份配置。
- 不把临时证据写到运行目录外，不持久化私人或闲聊，不默认生成关系图、妙搭页面或本地报告缓存。
