# lark-work-report

基于“飞书全貌”整理个人日报、周报、月报和指定周期工作总结的 Codex Skill。

它使用同一套采集与证据引擎，只按报告周期切换时间窗、聚合粒度、比较基线、调用预算和输出章节。重点不是把飞书内容全部搬进报告，而是从日历、消息、文档、任务、会议纪要等来源中建立可核验的工作证据。

## 核心能力

- 支持日报、周报、月报和自定义周期。
- 复用已有日报生成周报、复用周报生成月报，减少重复查询。
- 每个关键事实、结果、数字、完成状态和决策保留飞书来源锚。
- 把归属不明确但可能与工作相关的内容放入独立“待复核”章节。
- 默认只临时保留本次采集证据；用户明确选择后才持久化。
- 内置周期解析、正文抓取队列过滤和报告结构校验脚本。

## 隐私边界

采集采用“元数据预分类 → 受控正文核验”两段式流程：

1. 先依据元数据把候选预分类为 `work`、`uncertain`、`private` 或 `chatter`。
2. 确定为私人或闲聊的候选不抓正文，也不保留其标题、链接和详情。
3. 只有工作和待复核候选进入正文核验。
4. 最终报告只包含工作内容与独立的待复核区，不展示私人或闲聊的计数和详情。

这不是简单的“数据越少越好”，而是在尽量覆盖工作的前提下，把明确无关内容挡在正文读取之前。

## 前置条件

- 已安装支持 Skills 的 Codex 环境。
- Python 3；随附脚本只使用标准库。
- 已安装并完成认证的 `lark-cli`。
- 当前身份拥有所需飞书资源的访问权限。
- 环境中可用相应飞书 Skills，例如 `lark-calendar`、`lark-im`、`lark-doc`、`lark-task`、`lark-minutes` 和 `lark-vc`。

## 安装

将完整仓库克隆到 Codex 的 Skills 目录。若已设置 `CODEX_HOME`：

```bash
git clone https://github.com/Rocky-tc/lark-work-report.git "$CODEX_HOME/skills/lark-work-report"
```

未设置 `CODEX_HOME` 时，Codex 的常见默认目录为：

```bash
git clone https://github.com/Rocky-tc/lark-work-report.git ~/.codex/skills/lark-work-report
```

必须保留整个目录，不能只复制 `SKILL.md`；执行流程还依赖 `references/`、`scripts/` 和 `agents/`。安装后在新的 Codex 任务中调用。

## 配置

先确认 `lark-cli` 中已存在可用的 profile：

```bash
lark-cli profile list
```

调用 Skill 时可以直接说明要使用的 profile；若当前项目的 `AGENTS.md` 已规定默认 profile，则按项目约定执行。Skill 使用个人身份读取个人日历、云文档、消息、任务等资源，不会主动切换全局 profile 或扩大权限。

## 使用

```text
$lark-work-report 整理今天的日报
$lark-work-report 整理上周周报
$lark-work-report 生成上个月的月报
$lark-work-report 整理 2026-07-01 到 2026-07-15 的工作总结
```

可以附加范围和深度：

```text
$lark-work-report 整理上周周报，只看项目 A 和客户 B
$lark-work-report 深度整理本月工作总结，并保留候选供我复核
$lark-work-report 只给 Markdown 草稿，不创建飞书文档
```

默认输出一篇新的飞书文档，并回读校验。用户要求只给草稿时，跳过文档创建与回读。

## 目录结构

```text
lark-work-report/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── collection-policy.md
│   ├── evidence-model.md
│   ├── output-contract.md
│   ├── relevance-and-retention.md
│   └── report-profiles.md
├── scripts/
│   ├── prepare-fetch-queue.py
│   ├── resolve-period.py
│   └── validate-report.py
└── tests/
```

## 调用预算

预算包含创建飞书文档和回读验证的约 2 次调用：

| 模式 | 常规预算 | 硬上限 |
|---|---:|---:|
| 日报 | 10–15 | 18 |
| 周报 | 18–26 | 32 |
| 月报（有周报缓存） | 12–20 | 25 |
| 月报（无缓存） | 28–40 | 45 |
| 深度模式 | 35–50 | 50 |

一次采集同时输出多种报告时会复用同一证据账本。每增加一种飞书报告，通常只增加创建和回读约 2 次调用。

## 本地验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile scripts/*.py tests/*.py
```

## 当前边界

- V1 面向当前登录用户的个人工作报告，不生成团队报告。
- 不自动发送或提交报告，不修改现有飞书对象和权限。
- 不默认生成关系图、妙搭页面或本地报告缓存。
- 受接口能力和当前身份权限限制的来源会在覆盖说明中披露。
