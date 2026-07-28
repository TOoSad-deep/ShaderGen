# ShaderForge Program Spec 架构

`program_spec/` 承载 LayerPlanV1 与 ShaderProgramSpecV1 的当前安全契约：严格解析/规范化、可信层哈希重算、ValidationAttestation 签发与匹配。实现与本文件是当前依据，历史实验设计已归档。

## 真相层级定位

- LayerPlanV1 是视觉分析 Author 对参考图的结构化分层解读，永久 advisory，不参与 scorer、acceptance 与 `current_best`。
- ShaderProgramSpecV1 是模型生成并经安全校验的执行真相，绝不能由 `CompiledDslShader`/`GraphProgramKey` 派生或反向构造；本包不 import legacy DSL 与 Graph registry。

## 防伪边界（fail-closed）

- 模型语义输入只允许 `schema_version`、`fragment_source`、`uniform_schema`、`uniform_values`、`tunable_manifest`、`canvas`、`renderer_contract_id`；出现 `validation_attestation`、`author_identity` 或任何自报哈希字段（`*_sha256`/`*_hash`）即拒绝，未知字段同样拒绝。
- `source_sha256`/`binding_sha256`/`spec_sha256`/`plan_sha256` 一律由可信层对规范化 canonical JSON（key 排序、紧凑分隔符、拒绝 NaN）重算；`spec_sha256` 只排除 `validation_attestation`（避免自哈希循环），并绑定 canonical `author_identity` 全部字段（reference/plan/instruction/model_ref/prompt_version/sampling_params/role/parent_spec_sha256/reference_content_type/input_context_sha256/repair_context_sha256）——任一身份字段篡改都会导致重算失配与 attestation 失效。
- `author_identity` 由可信层按调用元数据绑定：`reference_sha256` 必填，refine/repair 必须绑定父 `spec_sha256`，initial 不得携带父 Spec。`sampling_params` 必须记录 Gateway 实际生效的采样身份（provider/实际 temperature/reasoning_effort/response_format/identity source），不得写请求假值；`reference_content_type` 绑定参考图媒体类型，`input_context_sha256` 绑定角色输入上下文（refine 含 current_render 哈希与 canonical 评估上下文）。发生结构修复时，`repair_context_sha256` 还绑定 repair Prompt version、首轮输出哈希、校验错误、Schema 以及首轮与第二次调用的实际身份，修复结果不得冒充原 Prompt 的直接输出；`LayerAuthorIdentity` 同样绑定 repair 上下文，全部参与 `plan_sha256`。
- 可执行真相是 "Spec + 匹配 attestation + 可信 ExecutionReceipt" 的组合：`match_attestation` 重算内容哈希、核对 validator version 与检查项清单，并用可信 issuer 验证 receipt 的 HMAC 与像素/源码/Spec 绑定；任一不匹配即不可执行。

## 校验分层

- `parsing.py` 负责类型、取值域、有限数值、一一对应等结构严格性；`validation/program_spec_safety.py` 负责哈希完整性、资源上限、GLSL 静态规则与可静态证明的规范整数有界循环（复用 V1 `validate_shader`，`max_loop_iterations` 默认 1024），并拒绝会创建或改写 token 的宏类预处理指令，防止用宏别名藏匿超大循环。
- `attestation.py` 不执行真实渲染：`issue_attestation` 只接受真实 prepare+draw 成功路径产出、由 Renderer 私有 signer 签发的 `ExecutionReceipt`；compile/link/draw 结论由 receipt 的存在性证明——调用方不得也无从手工填写执行结论（旧 `TrustedExecutionResult` 已删除）。

## 信任模型（receipt/capability）

- `receipt.py` 把 capability 拆成两层：`_TrustedReceiptSigner` 持有由 `secrets` 生成的**进程本地 HMAC key**（key 永不导出、永不持久化），是 Renderer 私有签发入口，类型与实例都**不从公共包导出**，只有 rendering 组合根经 `_renderer_receipt_signer()` 取得；`TrustedReceiptVerifier` 是 Runner/attestation 可见的 **verify-only** capability，结构上没有任何签发方法——runner 想伪造 receipt 在类型层面就不可能。生产路径唯一验证根是 `process_receipt_verifier()`；测试必须经 `_test_receipt_capabilities()` 显式构造隔离的 signer/verifier 对，绝不与生产 CLI 共享信任根。
- `ExecutionReceipt` 绑定 `source_sha256`、`spec_sha256`（签发必填）、RGB/PNG 像素哈希、renderer/GL/GLSL 运行身份、nonce 与签发时间；候选路径还要求 PNG hash 及 browser/GL/GLSL 关键 runtime metadata 非空，只能由真实 renderer（或测试内显式 test-only signer）在一次成功 prepare+draw 后就地签发。
- **同进程有效性**：进程重启后 key 改变，一切旧 receipt/attestation（含从私有 run 目录反序列化的）验证一律 fail-closed；attestation/receipt 只是同进程执行证明，**绝不是 durable 证据**，持久化仅为审计，不提供跨进程验证。
- 威胁模型是进程外伪造（手造/反序列化/篡改）；同进程对抗性代码无法防御（Python 内省），不在本机制范围内。

## 边界

- 本包纯确定性、纯 stdlib，不发起模型调用、不触碰浏览器与 Graph。
- uniform 优化只能沿 `tunable_manifest` 修改 `uniform_values`；源码/拓扑变化必须是新 Spec 并重新全量校验。
- 静态校验不是 GLSL 编译器，真实 compile/link/draw 结论必须由可信执行环境注入。
