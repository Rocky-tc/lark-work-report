# lark-work-report

基于“飞书全貌”整理个人日报、周报、月报和指定周期工作总结的通用 [Agent Skill](https://agentskills.io/)。

核心流程遵循开放的 `SKILL.md` 目录结构，不依赖某个特定智能体品牌。任何兼容 Agent Skills 的宿主都可以在具备相应数据能力时使用它。

## 核心能力

- 使用同一套采集与证据引擎生成日报、周报、月报和自定义周期报告。
- 复用已有日报生成周报、复用周报生成月报，减少重复查询。
- 每个关键事实、结果、数字、完成状态和决策保留飞书来源锚。
- 把归属不明确但可能与工作相关的内容放入独立“待复核”章节。
- 默认只临时保留本次采集证据；用户明确选择后才持久化。
- 内置周期解析、正文抓取队列过滤和报告结构校验脚本。

## 平台无关设计

Skill 核心只要求宿主把可用能力映射为以下操作：

| 操作 | 用途 |
|---|---|
| `identity.current` | 确认报告主体与时区 |
| `candidate.list` | 按数据域和时间窗列候选元数据 |
| `candidate.fetch` | 按稳定 ID 读取允许抓取的正文 |
| `report.create` | 可选：创建新的飞书报告文档 |
| `report.fetch` | 可选：回读并验证新文档 |

这些操作可以由宿主原生连接器、MCP、其他 Skills、`lark-cli` 或用户提供的导出材料实现。详细契约见 [capability-adapters.md](references/capability-adapters.md)。

适配器按以下顺序选择：

1. 宿主已经连接的飞书原生工具或 MCP；
2. 当前环境中已安装并认证的 `lark-cli`；
3. 用户提供的 Markdown、JSON、CSV、文档或消息导出材料。

不同数据域可以混用适配器。若没有实时飞书连接，Skill 仍可基于用户提供的材料生成报告，但必须披露覆盖范围，不能声称完成了飞书全貌采集。

## 隐私边界

采集采用“元数据预分类 → 受控正文核验”两段式流程：

1. 先依据元数据把候选预分类为 `work`、`uncertain`、`private` 或 `chatter`。
2. 确定为私人或闲聊的候选不抓正文，也不保留其标题、链接和详情。
3. 只有工作和待复核候选进入正文核验。
4. 最终报告只包含工作内容与独立的待复核区，不展示私人或闲聊的计数和详情。

这不是简单的“数据越少越好”，而是在尽量覆盖工作的前提下，把明确无关内容挡在正文读取之前。

## 运行要求

| 使用方式 | 所需能力 |
|---|---|
| 通用 Skill 加载 | 能识别 `SKILL.md` 并读取同目录的 `references/` |
| 运行确定性脚本 | Python 3；脚本仅使用标准库 |
| 实时采集飞书 | 已授权的飞书连接器、MCP，或已认证的 `lark-cli` |
| 基于导出材料生成 | 能读取用户提供的文件，不要求实时飞书连接 |
| 创建飞书文档 | 同一输出适配器同时支持创建和回读 |

现有飞书对象始终只读。只有最终交付阶段可以新建一篇报告文档；Skill 不发送报告、不修改权限，也不切换全局身份配置。

## 安装

克隆完整仓库：

```bash
git clone https://github.com/Rocky-tc/lark-work-report.git
```

把得到的 `lark-work-report/` 完整目录放入或导入宿主可识别的 Skills 位置，然后让宿主重新发现 Skills。具体位置或导入入口由宿主决定。

必须保持以下相对结构：

```text
lark-work-report/
├── SKILL.md
├── references/
└── scripts/
```

不能只复制 `SKILL.md`，因为执行流程依赖同目录中的参考文件和脚本。`agents/` 是可选的宿主界面元数据，核心流程不读取它。

## 数据适配

### 使用宿主连接器或 MCP

让宿主根据 [capability-adapters.md](references/capability-adapters.md) 把现有工具映射到统一操作。工具名不需要与示例一致，只要满足相同输入、输出和安全语义。

### 使用 `lark-cli`

先完成 `lark-cli` 安装和认证，再确认可用 profile：

```bash
lark-cli profile list
```

调用 Skill 时说明要使用的 profile，或由当前项目规则提供默认值。Skill 不修改全局 profile。

### 使用导出材料

直接把飞书导出的文档、消息、日历、任务或会议材料交给宿主，并说明时间范围。离线模式默认输出 Markdown 草稿。

## 使用

自然语言请求适用于所有宿主：

```text
使用 lark-work-report 整理今天的日报
使用 lark-work-report 整理上周周报
使用 lark-work-report 生成上个月的月报
使用 lark-work-report 整理 2026-07-01 到 2026-07-15 的工作总结
```

宿主支持显式 Skill 调用时，按宿主自己的语法选择 `lark-work-report`；否则直接使用上述自然语言请求。

可以附加范围、深度和交付方式：

```text
整理上周周报，只看项目 A 和客户 B
深度整理本月工作总结，并保留候选供我复核
只给 Markdown 草稿，不创建飞书文档
```

输出适配器能创建并回读飞书文档时，默认交付新文档；否则交付经过相同校验的 Markdown 草稿。

## 目录结构

```text
lark-work-report/
├── SKILL.md
├── agents/                  # 可选的宿主界面元数据
├── references/
│   ├── capability-adapters.md
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

预算统计外部工具、MCP 或 `lark-cli` 请求；本地脚本执行不计入外部调用。预算包含创建飞书文档和回读验证的约 2 次调用：

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

- V1 面向当前主体的个人工作报告，不生成团队报告。
- 不自动发送或提交报告，不修改现有飞书对象和权限。
- 不默认生成关系图、妙搭页面或本地报告缓存。
- 受接口能力和当前身份权限限制的来源会在覆盖说明中披露。
