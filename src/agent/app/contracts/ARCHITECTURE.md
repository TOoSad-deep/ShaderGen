# Contracts 架构

`src/agent/app/contracts/` 保存跨 State、Node、Graph 和具体适配器共享的中立契约。

## 当前文件

- `llm.py`：定义 `LLMGateway`、`LLMCallOptions`、`LLMResponse`、token usage、thinking、`text | json_object` 输出格式和统一错误；`EffectiveSamplingParams`/`EffectiveCallIdentity` 描述一次调用实际生效的采样参数与 provider/model/identity source（由 Gateway 按 family 事实填写，非请求值），`LLMResponse.effective_identity` 缺省 None，需要可信身份的调用方必须 fail-closed。
- `png_to_shader_min.py`：legacy MinScene Builder 的 typed patch 契约，默认产品 Graph 不使用。
- `shader_graph_author.py`：定义 ShaderGraph Refine Author 的单个 typed layer patch 判别联合（`operation` 判别），只允许 add_layer_bundle/remove_layer/replace_layer_bundle/reorder_layer/replace_canvas_background 五种原子 op；每个 patch 必须携带 `base_document_sha256`，应用时先比对 `document_sha256`，再以完整重建触发全图校验（id 唯一、层级/primitive/CSG 预算、opaque 背景），失败只抛稳定错误码。DSL shape 为内联树、无 id 引用，不可达节点在结构上不可表示。`summarize_shader_graph_author_patch()` 只公开 operation、layer id、节点类型集合、base hash 前缀和指纹。
- `png_to_shader_min_replay.py`：legacy MinScene Builder 的私有 replay schema 和 fail-closed reader；默认产品 Graph 不写该 bundle。
- `layerplan_glsl_shadow.py`：默认 direct engine 与历史 shadow A/B 共用的模型 JSON schema 与薄 adapter。**唯一 canonical 契约是 `shaderforge.program_spec`**：本模块不定义任何 LayerPlan/ProgramSpec 数据结构、规范化规则或哈希语义，`LayerPlanV1`/`ShaderProgramSpecV1`/`AuthorIdentity` 等名字只是 canonical 类型的再导出。保留的只有：发给模型的严格 JSON Schema、fail-closed JSON 预检、画布一致性检查与 direct GLSL 契约检查；fragment_source 必须满足 canonical `validate_shader` 全量静态规则，兼容 `u_image` 只能声明不可采样。`parse_*_semantics()` 把语义校验委托 canonical builder，`assemble_*()` 再绑定真实参考图、指令、父 Spec、current Render、metric 与修复上下文身份；篡改任一输入即哈希失配。

## 边界规则

- Contracts 不依赖 `llms`、`nodes`、`graphs`、`services` 或后端。
- Node 只通过 `LLMGateway` 使用模型能力。
- `response_format` 是供应商中立调用语义；业务 Node 不直接拼接 DashScope/OpenAI 的请求字典。
- 共享运行类型先进入 Contracts，不放在具体 model-family 适配器中。
- 模型角色契约默认 `extra=forbid`、不可变并带字段上限；Prompt 版本和 Scene/Patch 绑定由 Parser 继续做上下文校验。
- 契约变化影响 Node、Graph 或 Service 时，必须同步测试和对应架构文档。
