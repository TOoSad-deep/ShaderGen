# Services 架构

`agent.app.services` 是 Backend 调用 Agent 的唯一公共边界，暴露默认 direct engine 和隔离的 ShaderGraph fallback。

## Direct

- `layerplan_glsl_direct.py` 运行 VisualAnalysis LayerPlan 和 direct Initial/Refine。
- Runner 共用 canonical ProgramSpec safety、真实 Renderer receipt、metric、预算和 strict incumbent 选择。
- 结果中的 LayerPlan、ProgramSpec、Render 和原始错误留在私有 attempt；公开摘要只含安全状态、身份和指标。
- Service 不注册 LangGraph，也不发布 parent Artifact。

## ShaderGraph fallback

- `png_to_shader_min.py` 组合 Graph、LLM Gateway、私有 Artifact Store 和 run-scoped Renderer registry。
- fallback child 使用独立 Store、Renderer、cache 和预算，不复用 direct attempt 的可变状态。
- `read_public_artifact()` 只接受 `final-render`、`metrics`、`manifest`。
- Graph 正常终止和 Service 异常路径都幂等清理资源。

## 共同边界

- 模型不得提供可信哈希、身份或 attestation。
- direct 候选必须经过 canonical ProgramSpec、静态校验、真实 prepare/draw、Renderer receipt 和真实像素 metric。
- Backend 只依赖本包公共接口；Service 不持有数据库连接池。
- Memory/checkpoint 不由当前产品 Service 打开。

## 按需能力

`layerplan_glsl_shadow.py`、盲评、suite 和 promotion evidence 仍为休眠的质量实验实现。只有用户明确发起方案比较或上线准备时才读取、运行或扩建；它们不接产品 API，也不决定默认 engine。冻结协议在实现身份漂移后仅用于历史 `--verify`；suite live CLI 没有隐式默认协议，必须显式提供成对的当前 `--manifest`/`--gate`，且仍由实现身份检查 fail-closed。

`node_lab.py` 只在显式 factory 配置下把 ShaderGraph Node 接入独立 Node Lab。
