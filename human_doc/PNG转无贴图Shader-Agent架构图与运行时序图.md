# PNG 转无贴图 Shader Agent：架构图与运行时序图

本文是《PNG 转无贴图 Shader Agent 最终架构》的配套图示，使用 Mermaid 编写，可直接在支持 Mermaid 的 Markdown 编辑器中渲染。

V1 的具体实现任务与各 Agent Prompt 见 `PNG转无贴图Shader-Agent-V1实现计划与Prompt.md`。

---

## 1. 完整系统架构图

```mermaid
flowchart LR
    USER["用户"]

    subgraph FE["Frontend｜交互与预览"]
        UPLOAD["上传 PNG 与约束"]
        PROGRESS["运行进度与预算"]
        COMPARE["参考图 / 候选 / 残差对比"]
        PREVIEW["WebGL1 用户侧预览"]
        HITL["人工反馈与候选选择"]
    end

    subgraph BE["Backend｜API 与任务生命周期"]
        RUNAPI["Run API"]
        EVENTAPI["SSE / WebSocket Events"]
        ARTAPI["Artifact API"]
        RUNSERVICE["Run Service"]
    end

    subgraph AGENT["Agent｜LangGraph 语义外循环"]
        CONTEXT["Prepare Context"]
        ANALYZE["Analyze Target"]
        INTENT["Build Intent IR"]
        PLAN["Plan Strategy"]
        SEEDS["Propose Genome Seeds"]
        PATCH["Propose GenomePatch"]
        REVIEW["VLM Pairwise Review"]
        DECIDE["Decide Next"]
        FINALIZE["Finalize Run"]
    end

    subgraph SF["ShaderForge｜确定性执行内循环"]
        MEASURE["图像测量\nMask / BBox / Palette / ROI"]
        GENOME["Effect Genome"]
        VALIDATE["Schema / Contract / Safety Validator"]
        COMPILE["Genome → GLSL Compiler"]
        RENDER["真实 WebGL1 Renderer"]
        ORACLE["Deterministic Oracle\nGlobal + Local Loss"]
        SEARCH["Parameter Search Engine"]
        SELECT["Current Best Selector"]
        ARCHIVE["Candidate Archive"]
    end

    subgraph INFRA["Persistence 与基础设施"]
        CHECKPOINT["LangGraph Checkpointer"]
        MEMORY["Project Memory Store"]
        ARTIFACT["Artifact Store"]
        CACHE["Render / Score Cache"]
        LEDGER["Run Ledger / Events / Logs"]
        WORKER["Browser Renderer Worker Pool"]
    end

    USER --> UPLOAD
    UPLOAD --> RUNAPI
    RUNAPI --> RUNSERVICE
    RUNSERVICE --> CONTEXT

    CONTEXT --> MEASURE
    MEASURE --> ANALYZE
    ANALYZE --> INTENT
    INTENT --> PLAN
    PLAN --> SEEDS
    SEEDS --> GENOME

    GENOME --> VALIDATE
    VALIDATE --> COMPILE
    COMPILE --> RENDER
    RENDER --> ORACLE
    ORACLE --> SELECT
    SELECT --> ARCHIVE
    SELECT --> DECIDE

    DECIDE -->|"参数仍可优化"| SEARCH
    SEARCH --> GENOME
    DECIDE -->|"结构性停滞"| PATCH
    PATCH --> GENOME
    DECIDE -->|"候选晋级"| REVIEW
    REVIEW --> SELECT
    DECIDE -->|"达标或预算耗尽"| FINALIZE

    CONTEXT <--> CHECKPOINT
    CONTEXT <--> MEMORY
    FINALIZE --> MEMORY
    GENOME <--> CACHE
    ORACLE <--> CACHE
    RENDER <--> WORKER
    COMPILE --> ARTIFACT
    RENDER --> ARTIFACT
    ORACLE --> ARTIFACT
    FINALIZE --> ARTIFACT
    RUNSERVICE --> LEDGER

    RUNSERVICE --> EVENTAPI
    EVENTAPI --> PROGRESS
    ARTIFACT --> ARTAPI
    ARTAPI --> COMPARE
    ARTAPI --> PREVIEW
    COMPARE --> HITL
    HITL --> RUNAPI
    PREVIEW --> USER
```

### 图中最重要的边界

- Frontend 负责上传、展示、用户确认和兼容性预览，不承担唯一的自动迭代闭环。
- Backend 负责 HTTP、任务状态、事件和产物访问，不直接实现 Agent 节点或 Shader 算法。
- Agent 负责低频语义决策和结构修订，不进入每一次参数评估。
- ShaderForge 负责可重复的图像分析、编译、渲染、评分和搜索。
- WebGL1 Renderer 是运行时真值；Oracle 是自动接受候选的事实依据。
- `current_best` 只由确定性 Selector 更新，最后生成的候选不一定是最终结果。

---

## 2. 单次任务运行时序图

```mermaid
sequenceDiagram
    autonumber

    actor U as 用户
    participant FE as Frontend
    participant API as Backend Run API
    participant RS as Run Service
    participant G as LangGraph Controller
    participant CTX as Context / Memory
    participant TA as Target Analyzer
    participant AI as LLM / VLM Gateway
    participant SF as ShaderForge Core
    participant WR as WebGL1 Renderer
    participant OR as Oracle
    participant SE as Search Engine
    participant AS as Artifact Store
    participant EV as Event Stream

    U->>FE: 上传 PNG、文字约束和预算
    FE->>API: POST /api/shader/runs
    API->>RS: 创建 run_id、校验请求
    RS->>AS: 保存参考图与 request.json
    RS->>G: 启动 png_to_shader_graph
    RS-->>FE: 202 Accepted + run_id

    par 前端订阅进度
        FE->>EV: 订阅 run_id 事件
    and Agent 准备上下文
        G->>CTX: 按 project_id 获取约束、策略和历史经验
        CTX-->>G: ContextPack
    end

    G->>TA: 测量 PNG
    TA->>SF: 计算 mask、bbox、颜色、边缘、ROI、像素探针
    SF-->>TA: TargetMeasurements
    TA->>AI: 参考图 + 测量摘要，分析视觉层
    AI-->>TA: 视觉分层、模型假设、不确定项
    TA-->>G: Intent IR
    G->>AS: 保存 measurements、mask、Intent IR
    G->>EV: analysis.completed

    G->>AI: 选择策略并生成 3–5 个 Genome seed
    AI-->>G: Effect Genome seeds

    loop 对每个初始 Seed
        G->>SF: 校验 Genome、RenderContract 和数值安全

        alt 静态校验失败
            SF-->>G: ValidationErrors
            G->>AI: 请求一次受限 Genome 修复
            AI-->>G: GenomePatch
            G->>SF: 应用并重新校验
        else 静态校验通过
            SF-->>G: Validated Genome
        end

        SF->>SF: Genome 编译为 GLSL
        SF->>WR: compile + link + draw

        alt 编译或链接失败且仍有修复预算
            WR-->>G: CompileResult + GLSL log
            G->>AI: 根据结构化错误提出修复 Patch
            AI-->>G: GenomePatch
        else 渲染成功
            WR-->>SF: Render PNG + runtime metadata
            SF->>OR: 参考图、渲染图、目标 mask、ROI
            OR-->>SF: ScoreBreakdown + residual map
            SF-->>G: CandidateRecord
            G->>AS: 保存 Genome、GLSL、PNG、score 和 residual
            G->>EV: candidate.scored
        end
    end

    G->>SF: 选择满足硬约束的初始 current_best
    SF-->>G: current_best_id
    G->>EV: current_best.updated

    loop 有界优化循环：直到达标、停滞或预算耗尽
        G->>SF: 汇总 archive 和当前主要问题域
        SF-->>G: ArchiveSummary + ProblemDomain

        alt 当前拓扑仍有可调参数
            G->>SE: 优化一个参数块

            loop 参数候选评估
                SE->>SF: 生成参数候选 Genome
                SF->>WR: 编译并渲染
                WR-->>SF: RenderResult
                SF->>OR: 计算全局和局部损失
                OR-->>SF: ScoreBreakdown
                SF-->>SE: 候选评分
            end

            SE-->>G: 阶段最佳候选和改善摘要

        else 发生结构性停滞且仍有 Patch 预算
            G->>AI: 残差证据 + archive 摘要，提出 GenomePatch
            AI-->>G: 类型化 GenomePatch
            G->>SF: 校验并应用 Patch
        else 候选需要视觉晋级判断
            G->>AI: 参考图 + 候选 A/B + 固定 rubric
            AI-->>G: Pairwise 选择、置信度和区域问题
        end

        G->>SF: 执行硬约束与单调接受判定

        alt 新候选满足接受规则
            SF-->>G: 更新 current_best
            G->>EV: current_best.updated
        else 新候选退化或证据不足
            SF-->>G: 保留旧 current_best
        end

        G->>G: 检查质量、停滞、时间、渲染次数和模型预算
    end

    G->>CTX: 晋升精炼后的约束、策略、评审和失败经验
    G->>AS: 固化最佳 GLSL、PNG、Genome、HTML 和 manifest
    G->>EV: run.completed + stop_reason
    EV-->>FE: 最终状态与 artifact refs
    FE->>API: 获取最佳产物
    API->>AS: 读取 final artifacts
    AS-->>FE: GLSL、预览图、Genome、评分与复现记录
    FE-->>U: 展示和下载最终结果
```

---

## 3. 运行时的候选接受子流程

这张小图强调 `current_best` 为什么不会被最后一次失败修改覆盖。

```mermaid
flowchart TD
    C["新 Candidate"] --> S{"静态安全与无贴图约束通过？"}
    S -->|否| REJECT["拒绝候选并保存诊断"]
    S -->|是| W{"WebGL compile / link / draw 通过？"}
    W -->|否| REPAIR{"仍有修复预算？"}
    REPAIR -->|是| PATCH["生成受限 GenomePatch"]
    PATCH --> C
    REPAIR -->|否| REJECT
    W -->|是| SCORE["计算 Global + Local Score"]
    SCORE --> D{"目标问题域改善达标？"}
    D -->|否| REJECT
    D -->|是| P{"保护区域退化超限？"}
    P -->|是| REJECT
    P -->|否| T{"总损失改善或明确批准权衡？"}
    T -->|否| REJECT
    T -->|是| ACCEPT["更新 current_best"]
    REJECT --> KEEP["保留旧 current_best"]
    ACCEPT --> ARCHIVE["写入 Candidate Archive 与证据链"]
    KEEP --> ARCHIVE
```

---

## 4. 运行时阶段概览

```text
提交任务
  → 上下文准备
  → PNG 确定性测量
  → VLM 视觉分层
  → Intent IR
  → 策略与 Genome seeds
  → 校验 / 编译 / WebGL 渲染
  → Oracle 评分
  → 选出 current_best
  → 参数优化或结构 Patch
  → VLM / HITL 晋级评审
  → 达标、停滞或预算耗尽
  → 固化最佳 GLSL 与复现证据
```

运行时的关键闭环是：

```text
Genome
  → Validate
  → Compile
  → Render
  → Score
  → Select Best
  → Optimize Parameters / Propose Patch
  → Genome
```

整个循环必须有最大时间、最大渲染次数、最大模型调用数、最大修复次数和最大结构 Patch 次数，任何分支都不能形成无界循环。

---

## 5. 主 Agent 与子 Agent 关系图

最终架构为 1 个主控 Agent 加 4 个专业子 Agent；V1 只启用前三个专业子 Agent。

```mermaid
flowchart TD
    MAIN["PngToShaderOrchestrator\n主控：阶段、预算、路由、停止"]

    A1["VisualAnalysisAgent\nV1：视觉分层与 Intent"]
    A2["ShaderAuthorAgent\nV1：初稿、编译修复、有限视觉修订"]
    A3["VisualCriticAgent\nV1：区域化视觉诊断"]
    A4["StructureEvolutionAgent\nV2：结构停滞后的 GenomePatch"]

    MAIN --> A1
    MAIN --> A2
    MAIN --> A3
    MAIN -.->|"V2 启用"| A4

    subgraph TOOLS["确定性能力：不是子 Agent"]
        T1["Image Measurement"]
        T2["Contract / Safety Validator"]
        T3["WebGL1 Renderer"]
        T4["Deterministic Oracle"]
        T5["Parameter Search Engine"]
        T6["Current Best Selector"]
        T7["Artifact / Cache / Memory"]
    end

    A1 --> T1
    A2 --> T2
    A2 --> T3
    A3 --> T4
    A4 --> T5
    MAIN --> T6
    MAIN --> T7
```

### V1 子 Agent 清单

1. `VisualAnalysisAgent`：理解参考图，但不直接生成 GLSL。
2. `ShaderAuthorAgent`：生成初稿，并处理编译修复与有限视觉修订。
3. `VisualCriticAgent`：比较参考图与渲染图，输出区域化诊断。

V1 的三个子 Agent 都由 `PngToShaderOrchestrator` 调度。它们可以实现为 LangGraph 类型化节点或子图，不需要拆成三个独立服务。
