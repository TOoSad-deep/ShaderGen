# Graphs 架构

`src/agent/app/graphs/` 保存 LangGraph 图入口。当前只注册 `png_to_shader_min`。

## 当前图

- `png_to_shader_min_graph.py`：`scene_mvp` 最小技术链路，入口对象为 `png_to_shader_min_graph`。
- `png_to_shader_min_routing.py`：最小图的 3 个纯路由函数。

## Graph 规则

- Graph 只负责编排；模型消息、确定性处理和 Artifact 写入由 Node 承担。
- Builder 通过 `LLMGateway`、Renderer registry 和 Artifact Store 注入运行依赖。
- 每个 `*_graph.py` 必须在 Builder 上方维护 ASCII 图，本文件维护同名 Mermaid 区块。
- 新增、删除、重命名节点或修改边、路由、循环、终止路径和 `current_best` 安全边界时，必须同步源码 ASCII、本文 Mermaid、路由表与 `langgraph.json`。
- 完成 Graph 改动前运行 `make docs-check` 和 `uv run langgraph validate`。

## `png_to_shader_min_graph` 最小骨架

<!-- graph-diagram:png_to_shader_min_graph:start -->
```mermaid
flowchart TD
    START([START])
    END([END])
    START --> initialize_run[initialize_run]
    initialize_run --> perceive_target[perceive_target]
    perceive_target --> author_initial[author_initial]
    author_initial --> materialize_shader[materialize_shader]
    materialize_shader --> render_and_evaluate[render_and_evaluate]
    render_and_evaluate --> decide_after_render[decide_after_render]
    decide_after_render -. optimize_base .-> optimize_base[optimize_base]
    decide_after_render -. finalize .-> finalize[finalize]
    optimize_base --> decide_after_base[decide_after_base]
    decide_after_base -. optimize_feature .-> optimize_feature[optimize_feature]
    decide_after_base -. author_refine .-> author_refine[author_refine]
    decide_after_base -. finalize .-> finalize
    optimize_feature --> decide_after_feature[decide_after_feature]
    decide_after_feature -. optimize_feature .-> optimize_feature
    decide_after_feature -. author_refine .-> author_refine
    decide_after_feature -. finalize .-> finalize
    author_refine --> materialize_shader
    finalize --> END

    classDef safety fill:#fff4d6,stroke:#ad7200,stroke-width:2px
    class render_and_evaluate,optimize_base,optimize_feature,finalize safety
```
<!-- graph-diagram:png_to_shader_min_graph:end -->

### 条件路由表

| 决定节点 | 路由函数 | 结果 | 下一节点 | 含义 |
|---|---|---|---|---|
| `decide_after_render` | `route_after_render` | `optimize_base` | `optimize_base` | Initial 首帧后进入 canvas base sweep；Refine 候选完成严格选择后经 no-op 过桥 |
| 同上 | 同上 | `finalize` | `finalize` | 已达标、失败或预算耗尽 |
| `decide_after_base` | `route_after_base` | `optimize_feature` | `optimize_feature` | 获胜 ShaderDocument 的稳定参数 block queue 非空 |
| 同上 | 同上 | `author_refine` | `author_refine` | 参数 block 已耗尽且仍有模型与 Refine 预算 |
| 同上 | 同上 | `finalize` | `finalize` | 已达标或预算耗尽 |
| `decide_after_feature` | `route_after_feature` | `optimize_feature` | `optimize_feature` | 消费下一个稳定 `layer:<id>.*` / `node:<id>.*` 参数 block；节点名为兼容路由保留 |
| 同上 | 同上 | `author_refine` | `author_refine` | 参数 block queue 已空且仍可 Refine |
| 同上 | 同上 | `finalize` | `finalize` | 已达标或预算耗尽 |

- 黄色节点构成 `current_best` 安全边界。产品组合根中，模型 ShaderDocument、感知直接产出的 fallback、node-id 参数候选和 typed layer patch 都必须经 specialized Compiler 与真实 WebGL1 渲染，并按 `min_scene_composite_v3` 严格改善后才能提交。
- `current_best` 是不可变 `ShaderGraphCandidateSnapshot`，绑定 document/compiler/program key/render/metric、父文档 hash 与 provenance；Prepared handle 只存在于 run-scoped registry，不进入 State 或 Artifact。
- Refine 永远从只读 `current_best.document` 派生一个绑定 `base_document_sha256` 的 typed layer patch；非法、重复、Renderer 失败或 loss 未严格改善的分支整体丢弃。
- Service 按 `9 + 2F + 6R` 推导 run 级 recursion limit，并增加 4 步余量；超过全局安全上限的配置在加载时拒绝。
- 公式中的 `F` 在产品 ShaderGraph 组合根表示最多 12 个有界参数 block，不再表示 MinScene Feature；当前 YAML 的上限仍在全局 256 防御线内。
- 同一 topology/active block 通过 `compiled_graph_program_cache_v1` 复用 packed uniform program；结构变化产生新 key，compile/cache 均有硬上限。`finalize` 和 Service `finally` 幂等关闭资源。
