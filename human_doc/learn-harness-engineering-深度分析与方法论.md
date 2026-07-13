# Learn Harness Engineering 深度分析与可执行方法论

生成日期：2026-07-07  
分析对象：[Learn Harness Engineering 中文站点](https://walkinglabs.github.io/learn-harness-engineering/zh/) 的讲义部分，参考源仓库版本 `2fdc46b`。

## 一句话结论

这套讲义的核心不是“写更好的 prompt”，而是把 AI 编程 agent 的工作变成一个可运行、可验证、可交接、可持续改进的工程系统。模型能力只是起点，真正决定交付可靠性的，是模型外部的 harness：指令、工具、环境、状态和反馈。

可以把课程压缩成一句方法论：

> 先让仓库成为事实来源，再用小任务、硬边界、可执行验证、运行时信号和清洁交接，把 agent 从“会写代码”约束成“能稳定交付”。

## 讲义主线

| 讲义 | 解决的问题 | 最终要留下的工程产物 |
|---|---|---|
| 01. 模型能力强，不等于执行可靠 | 失败不一定是模型差，常是 harness 缺陷 | 失败归因表、显式完成定义、初始 `AGENTS.md` |
| 02. Harness 到底是什么 | prompt 文件不等于 harness | 指令、工具、环境、状态、反馈五子系统 |
| 03. 仓库成为唯一事实来源 | 隐性知识对 agent 不存在 | 仓库内的架构、运行、验证、进度文档 |
| 04. 拆分指令文件 | 巨型指令文件降低信噪比 | 短入口文件 + 按需读取的专题文档 |
| 05. 跨会话连续性 | 长任务会丢上下文和决策原因 | `PROGRESS.md`、`DECISIONS.md`、git 检查点 |
| 06. 独立初始化阶段 | 初始化和实现混在一起会两头失败 | 启动就绪清单、示例测试、任务分解 |
| 07. 任务边界 | agent 容易过度延伸、半途而废 | WIP=1、范围表面、完成证据 |
| 08. 功能清单 | “做完”标准不能只在对话里 | 机器可读的 feature list 状态机 |
| 09. 防止提前宣告完成 | agent 系统性过度自信 | 三层终止校验、外部完成判定 |
| 10. 端到端测试 | 单元测试看不到跨组件缺陷 | E2E 验证、可执行架构规则、可操作错误消息 |
| 11. 可观测性 | agent 运行过程不能是黑盒 | 运行时信号、冲刺合同、评分标准 |
| 12. 清洁交接 | 会话结束不清理会导致熵增 | 退出检查表、质量文档、周期清理循环 |

## 深度分析

### 1. 课程真正讨论的是“控制系统”，不是“提示词技巧”

普通 prompt 的模式是：把任务描述清楚，然后希望模型照做。Harness engineering 的模式是：为 agent 建一个控制系统，让它在每一步都被事实约束。

这个控制系统至少包含五件事：

- 目标在哪里：入口文件、功能清单、任务合同。
- 知识在哪里：仓库内的架构文档、模块约束、决策日志。
- 环境怎么跑：固定依赖、启动命令、验证命令。
- 状态怎么延续：进度文件、git 检查点、交接记录。
- 做完怎么判定：测试、端到端流程、运行时信号、独立评估。

因此，一个 agent 失败时，第一反应不应该是“模型不够聪明”，而是问：它缺了哪一类控制面？

### 2. 失败模式可以归为九类

| 失败模式 | 表面症状 | 根因 | Harness 修法 |
|---|---|---|---|
| 需求模糊 | agent 自己补需求 | 任务规范缺失 | 每个任务写行为、验证命令、排除项 |
| 隐性规则缺失 | 违反团队约定 | 知识不在仓库 | 把约束写进仓库，靠近相关代码 |
| 环境不可复现 | 花时间修依赖 | 环境子系统弱 | 锁版本、写 setup/dev/check 命令 |
| 验证缺口 | “做完了”但不能跑 | 完成判定主观 | 三层终止校验 + E2E |
| 状态丢失 | 新会话重复探索 | 缺持久化状态 | `PROGRESS.md`、`DECISIONS.md` |
| 指令膨胀 | 规则很多但不遵守 | 信噪比下降 | 短入口 + 专题文档 |
| 任务越界 | 改很多文件但无功能通过 | WIP 过高 | WIP=1，先完成当前功能 |
| 黑盒执行 | 不知道错在哪里 | 可观测性不足 | 采集日志、trace、评分证据 |
| 会话熵增 | 下个会话先收拾烂摊子 | 退出无纪律 | 清洁状态作为完成条件 |

这些失败不是互相独立的。最常见的链条是：

1. 需求没有拆成可验证功能。
2. agent 同时启动多个方向。
3. 单元测试或静态检查局部通过。
4. agent 提前宣称完成。
5. 会话结束时没有记录状态。
6. 下个会话重新猜，继续在半成品上叠半成品。

Harness 的价值，就是在每个环节加一条机械防线。

### 3. “仓库即规范”是整套课程的地基

讲义反复强调：agent 看不到的东西，对它来说就不存在。Slack 讨论、Jira 票据、Confluence 文档、团队成员脑子里的习惯，全都不是可靠输入。对 agent 来说，稳定可见的事实来源主要是仓库。

所以“给 agent 上下文”的正确姿势不是聊天时多解释几句，而是把信息变成仓库工件：

- 项目是什么：`AGENTS.md` / `CLAUDE.md`
- 怎么运行：`Makefile`、`package.json scripts`、`init.sh`
- 架构边界：模块旁边的 `ARCHITECTURE.md`
- 当前进度：`PROGRESS.md`
- 为什么这么做：`DECISIONS.md`
- 还差什么：`feature_list.json` 或 `features.md`
- 怎么判定完成：验证命令和通过证据

一句实用规则：如果一个新会话不能只看仓库回答“这是什么、怎么跑、怎么测、做到哪了、下一步是什么”，仓库就还不是合格的 harness。

### 4. 可靠性来自“外部化”，不是来自 agent 自律

讲义里最重要的工程思想是外部化：

- 把隐性知识外部化为文档。
- 把任务范围外部化为功能清单。
- 把完成标准外部化为验证命令。
- 把会话记忆外部化为进度和决策日志。
- 把质量判断外部化为评分标准。
- 把运行状态外部化为日志、trace、健康检查。

原因很简单：agent 的自我判断不稳定，尤其在复杂任务、长上下文和临近收尾时更容易乐观。外部化以后，系统不再依赖“agent 觉得做完了”，而是依赖可检查的事实。

### 5. “少做但做完”是默认策略

课程对 agent 的任务管理非常克制：WIP=1。一次只允许一个功能处于 active 状态，通过验证后再进入下一个。

这不是保守，而是因为 agent 的推理预算会被并发任务稀释。一个会话里同时开五个功能，最终常见结果不是五个功能都快了，而是五个功能都半成品。功能完成率比代码行数更重要。

## 可执行方法论：Harness 工作闭环

下面这套流程适用于用 Codex、Claude Code、Cursor 或其他 coding agent 做真实工程任务。先从最小版本开始，别一次搭太复杂。

### 阶段 0：先诊断，不先换模型

每次失败先归因到一层：

| 层 | 要问的问题 | 最小修复 |
|---|---|---|
| 任务规范 | 任务是否有行为、边界、完成定义？ | 补一段任务合同 |
| 上下文 | 关键规则是否在仓库里？ | 写入 `AGENTS.md` 或模块文档 |
| 环境 | agent 能否一条命令启动和验证？ | 补 `make setup/dev/check` |
| 反馈 | 失败信息是否告诉它怎么修？ | 改错误消息和测试输出 |
| 状态 | 新会话能否接上？ | 补进度、决策和下一步 |

不要写“模型失败”。写“失败层：验证反馈缺失；证据：agent 未运行 E2E 就声明完成；修复：把 E2E 加入完成门禁”。

### 阶段 1：建立最小仓库事实来源

最小文件结构：

```text
project/
├── AGENTS.md
├── Makefile
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   └── FEATURES.md
├── PROGRESS.md
└── src/
```

`AGENTS.md` 保持短，像路由器，不像百科全书：

```markdown
# AGENTS.md

## 项目概览
一句话说明项目用途、主要技术栈、当前目标。

## 常用命令
- 安装：make setup
- 启动：make dev
- 测试：make test
- 完整验证：make check

## 硬约束
- 每次只处理一个 active 功能。
- 未通过验证不得标记完成。
- 涉及跨组件行为必须跑端到端检查。
- 会话结束前必须更新 PROGRESS.md。

## 按需阅读
- 架构边界：docs/ARCHITECTURE.md
- 功能清单：docs/FEATURES.md
- 决策记录：docs/DECISIONS.md
```

判断标准：新 agent 会话 5 分钟内能说清项目、命令、当前任务和验证方式。

### 阶段 2：独立初始化，不急着写功能

第一次会话只做初始化：

- 环境能安装。
- 应用能启动。
- 至少一个测试能通过。
- `make check` 或等价命令存在。
- 功能清单有至少 3 个可执行任务。
- 初始状态已提交到 git。

初始化验收清单：

```markdown
- [ ] 从空环境运行 setup 成功
- [ ] dev/start 命令成功
- [ ] test 命令至少跑通一个测试
- [ ] check 命令包含必要验证
- [ ] FEATURES/PROGRESS/DECISIONS 文件存在
- [ ] 全新会话能接手下一步
```

能用模板就用模板。不要让 agent 从空目录推断项目结构，除非任务本身就是探索模板。

### 阶段 3：把需求变成功能状态机

功能清单不是备忘录，而是调度器、验证器、交接器的共同数据源。

最小格式：

```markdown
# FEATURES.md

| id | behavior | verification | state | evidence |
|---|---|---|---|---|
| F01 | 用户可以提交注册表单并创建账号 | make e2e AUTH_REGISTER | passing | commit abc123 |
| F02 | 重复邮箱注册返回明确错误 | make e2e AUTH_DUP_EMAIL | active |  |
| F03 | 用户可以登录并获得 session | make e2e AUTH_LOGIN | not_started |  |
```

状态只用四个：

- `not_started`
- `active`
- `blocked`
- `passing`

规则：

- 任意时刻最多一个 `active`。
- 只有验证命令通过，才能改成 `passing`。
- `passing` 必须带证据。
- 阻塞项必须写明阻塞原因和解除条件。

### 阶段 4：用 WIP=1 执行任务

每次给 agent 的任务只包含一个功能项：

```markdown
当前任务：F02

行为：重复邮箱注册返回明确错误。
通过标准：make e2e AUTH_DUP_EMAIL 成功。
范围内：API 校验、错误响应、必要测试。
范围外：重构认证模块、改 UI 样式、优化数据库索引。
完成后：更新 FEATURES.md 和 PROGRESS.md。
```

这会刻意牺牲“看起来的速度”，换取真正完成率。对 agent 项目来说，已验证功能数比新增代码行数更有意义。

### 阶段 5：完成判定外部化

不要让 agent 自己决定“做完了”。使用三层终止校验：

| 层级 | 检查内容 | 例子 |
|---|---|---|
| L1 静态 | 格式、lint、类型、构建 | `make lint && make typecheck && make build` |
| L2 行为 | 单元/集成测试、启动检查 | `make test && make smoke` |
| L3 系统 | 端到端用户流程 | `make e2e F02` |

完成规则：

- L1 不过，不能进入 L2。
- L2 不过，不能进入 L3。
- L3 不过，不能标记 passing。
- 核心功能没验证前，不允许“顺便重构”。

错误消息要能指导修复：

```text
ERROR: F02 failed.
WHAT: POST /register duplicate email returned 500.
WHY: Duplicate email should map to 409 with USER_EXISTS code.
FIX: Catch unique constraint error in auth service and return typed API error.
CHECK: rerun make e2e AUTH_DUP_EMAIL.
```

### 阶段 6：把架构规则变成可执行检查

文档里的架构规则容易被忽略。能机械检查的，都变成 lint、测试或脚本。

例子：

```markdown
规则：渲染层不能直接访问文件系统。
检查：扫描 renderer 目录中是否 import fs。
错误：说明为什么错，以及应该移到哪个边界。
```

每次 code review 里出现重复意见，就问一句：能不能把它提升为自动检查？能就加入 harness。

### 阶段 7：给运行过程加可观测性

最低配可观测性：

- 启动是否成功，何时 ready。
- 当前功能路径执行到了哪一步。
- 关键副作用是否发生：DB 写入、文件生成、API 调用。
- 失败时的输入、错误、调用链。
- 每个任务的验证结果和证据位置。

复杂任务再加过程可观测性：

```markdown
# Sprint Contract

## 范围
- 本轮只实现 F02。

## 验证标准
- 重复邮箱返回 409。
- 响应体包含 USER_EXISTS。
- 正常注册路径不被破坏。

## 排除项
- 不改 UI。
- 不做性能优化。
```

对主观质量任务，增加评分标准和独立 evaluator。不要让同一个 agent 既交卷又当唯一阅卷人。

### 阶段 8：会话结束必须清洁交接

清洁状态五条件：

- 构建通过。
- 测试通过。
- 进度已记录。
- 临时工件已清理。
- 标准启动路径可用。

退出检查表：

```markdown
## 会话退出检查

- [ ] make check 通过，或失败原因已记录
- [ ] 当前 feature 状态已更新
- [ ] PROGRESS.md 已更新
- [ ] DECISIONS.md 记录了重要取舍
- [ ] 无调试文件、临时日志、无解释 TODO
- [ ] 下一步任务明确
```

`PROGRESS.md` 最小模板：

```markdown
# PROGRESS.md

## 当前状态
- commit:
- check:
- active feature:

## 已完成
- 

## 未验证/问题
- 

## 下一步
1. 
```

`DECISIONS.md` 最小模板：

```markdown
# DECISIONS.md

## YYYY-MM-DD: 决策标题
- 决策：
- 原因：
- 否决方案：
- 后续影响：
```

### 阶段 9：周期性简化 harness

Harness 会腐化，也会过时。每月做一次轻量审计：

- 哪些规则没人用？
- 哪些文档和代码不一致？
- 哪些检查总是假阳性？
- 哪个组件移除后结果没有变差？
- 哪类失败出现了 3 次以上，应该自动化？

原则：能删就删，能自动化就自动化，能靠标准命令解决就别写复杂系统。

## 最小落地路线

### 30 分钟版本

适合马上开始用 agent 的现有项目：

1. 写一个短 `AGENTS.md`。
2. 明确 `setup/dev/test/check` 命令。
3. 写 `PROGRESS.md`，记录当前状态和下一步。
4. 给当前任务写一个明确完成标准。
5. 要求会话结束前更新进度并跑验证。

### 1 天版本

适合一个团队准备系统性使用 coding agent：

1. 把隐性架构约束写入仓库。
2. 拆一个 `docs/ARCHITECTURE.md`。
3. 建 `FEATURES.md`，每个功能都有验证命令。
4. 把 WIP=1 写进入口规则。
5. 加至少一个端到端 smoke test。
6. 加退出检查表。

### 1 周版本

适合持续开发项目：

1. 所有重复 code review 意见转成自动检查。
2. 每个模块补最小质量说明。
3. 为关键用户路径建立 E2E。
4. 为复杂功能引入 sprint contract。
5. 建立失败归因日志。
6. 每周做一次清理循环。

## 关键指标

| 指标 | 含义 | 目标 |
|---|---|---|
| 新会话重建时间 | 新 agent 恢复到可执行状态的时间 | 3-5 分钟内 |
| Verified Completion Rate | passing 功能数 / 已启动功能数 | 接近 1 |
| 提前完成率 | agent 声称完成但验证失败的比例 | 趋近 0 |
| E2E 捕获率 | E2E 发现而单测没发现的问题数 | 用来证明 E2E 价值 |
| 知识可见性缺口 | 关键规则中不在仓库的比例 | 低于 10% |
| 指令信噪比 | 当前任务相关指令 / 总指令 | 越高越好 |
| 清洁退出通过率 | 会话退出检查通过次数 / 总会话数 | 高于 95% |
| 重复实现次数 | 新会话重复做已完成工作的次数 | 趋近 0 |

## 常见反模式

- 把 `AGENTS.md` 写成 600 行百科全书。
- 只给“实现搜索功能”这种宽泛任务。
- 没有验证命令，却要求 agent 自己判断完成。
- 只有单元测试，没有真实用户路径。
- 在核心功能没跑通前顺手重构。
- 会话结束不更新进度。
- 把关键规则放在聊天记录、群消息或人的脑子里。
- 失败后直接换模型，而不修 harness。

## 最后压缩成四条工作纪律

1. 先地图，后上路：仓库必须回答项目、架构、命令、进度、验证。
2. 先一件，后下一件：WIP=1，一个功能通过后再开下一个。
3. 先运行，再宣告：静态、行为、端到端三层验证过了才算完成。
4. 先交接，再退出：进度、决策、证据和清洁状态必须留给下个会话。

## 资料来源

- [Learn Harness Engineering 中文首页](https://walkinglabs.github.io/learn-harness-engineering/zh/)
- [第一讲：模型能力强，不等于执行可靠](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-01-why-capable-agents-still-fail/)
- [第二讲：Harness 到底是什么](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-02-what-a-harness-actually-is/)
- [第三讲：让代码仓库成为唯一的事实来源](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-03-why-the-repository-must-become-the-system-of-record/)
- [第四讲：把指令拆分到不同文件里](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-04-why-one-giant-instruction-file-fails/)
- [第五讲：让跨会话的任务保持上下文连续](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-05-why-long-running-tasks-lose-continuity/)
- [第六讲：让 agent 每次工作前先初始化](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-06-why-initialization-needs-its-own-phase/)
- [第七讲：给 agent 划清每次任务的边界](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-07-why-agents-overreach-and-under-finish/)
- [第八讲：用功能清单约束 agent 该做什么](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-08-why-feature-lists-are-harness-primitives/)
- [第九讲：防止 agent 提前宣告完成](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-09-why-agents-declare-victory-too-early/)
- [第十讲：跑通完整流程才算真正验证](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-10-why-end-to-end-testing-changes-results/)
- [第十一讲：让 agent 的运行过程可观测](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-11-why-observability-belongs-inside-the-harness/)
- [第十二讲：每次会话结束前都做好交接](https://walkinglabs.github.io/learn-harness-engineering/zh/lectures/lecture-12-why-every-session-must-leave-a-clean-state/)
- [OpenAI: Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic: Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)
