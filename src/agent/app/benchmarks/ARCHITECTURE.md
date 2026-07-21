# Agent Benchmarks 架构

`src/agent/app/benchmarks/` 保存显式离线运行、可产生模型费用并输出冻结证据的 Agent benchmark。它不是 Backend-facing Service，也不会被在线 Graph 或 HTTP 路径导入。

## 当前模块

- `model_roles.py`：通过 Node Lab Application 执行 PNG-to-Shader V1 的五个生产模型角色；fixture 模式默认离线，real 模式要求 CLI、服务端环境和 Gateway 三重门禁，并执行 semantic/repair/token/wall/cost 全套硬预算。
- `v2_rendered_gate_collector.py`：V2.3 离线 actual-render 门禁的 Agent 侧证据收集器。它只从 `LocalPngToShaderV2StateStore` 的最后 confirmed State v4、全部 Candidate v3 closure 与密封的 concrete Chromium replay runner 派生 case；任何恢复、closure 或 replay 失败都签发显式 non-ready capability，不得跳过 case。
- `scripts/run_v2_3_rendered_structure_benchmark.py` v2 是 collector 的完整 visible suite 编排入口：显式 `--suite-run-id`，exclusive-create 输出目录，先冻结 development regression 10 + validation 41，再以一个 suite 级 concrete Chromium worker 运行 production V2 development Service/Graph；Graph 每次 physical render 获得 no-op-close 借用句柄，底层 worker 只在 suite `finally` 关闭。随后用独立 concrete replay runner 重放每个 State 的全部 Candidate，并在同一进程把 51 个 capability 交给正式 gate。

## 边界规则

- 可以依赖 `agent.app.services.node_lab` 组合独立 Harness，但在线 service 不得反向依赖本包。
- 必须通过生产 `NodeProvider`、Node、Prompt 和 Parser 运行，不复制节点语义。
- 失败、中断、恢复、manifest、source fingerprint 和报告语义只增不改；不得覆盖 M5 发布证据。
- V2.3 collector 返回不可序列化 `CollectionResult`：其中 capability 只能在同一进程交给正式 gate，逐 Candidate replay receipts 则由离线 runner 内容寻址持久化；普通 outcome、PIL 结果、fixture ref 或自定义 StateStore/renderer 都不能进入正式 admission。
- strict runner 的 fixture 只复用 V2.1 taxonomy/标签构造 Intent 输入，不代表 production VLM 语义；模型调用、token、cost 和 production admission 恒为 0/false。每例 wall/render/candidate/artifact、suite Graph 上限与独立 replay render 上限都写入 config；结束时逐 case 闭合 suite worker 的 Graph 实测调用，并闭合成功 replay receipts 的全部 item 调用。release-held-out 不读取；Service、State、Artifact 或 Chromium 启动/重放失败仍产生失败 capability 并保留在 51 例分母。
- collector 的 `expected_hypothesis_count` 取同一组 Measurements、Interpretation、ConstraintSet、Context 经 production `build_intent_variants()` hard-constraint 过滤后的 `variants` 数，不得使用原始 `target_hypotheses` 数。fixture policy v2 同时冻结 source、feasible、rejected 数和 rejection reason 聚合；旧 runner v1 对多 hypothesis/单 feasible case 会误拒绝，其已有输出只作 superseded audit，禁止续跑或进入门禁。
- `__init__.py` 不聚合具体 runner，CLI 和测试从明确模块导入，避免普通 Agent 导入加载 benchmark 依赖。
