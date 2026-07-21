# ShaderForge Store 架构

`store/` 保存 ShaderForge 运行的大产物和可复现证据。它与 Agent Memory 分离：Memory 只保存精炼摘要和 artifact id/hash，不复制图片、GLSL 或完整评分。

## 当前能力

当前只实现 `LocalArtifactStore`；M3 在不扩张 Store 职责的前提下用它保存完整 run/candidate 证据：

- 按 `project_id/run_id` 隔离运行目录；
- 写入 bytes、UTF-8 文本和稳定 JSON；
- 临时文件、`fsync`、原子 `os.replace`；
- 返回相对路径、SHA-256、字节数和 content type；
- 拒绝绝对路径、`..`、非法标识和 symlink 逃逸。
- `register_run()` 用内部 `.run-index/{run_id}.json` 持久映射 project/run，并在单进程顺序调用范围内拒绝同一 run_id 跨项目碰撞；客户端不能控制索引内容。
- M3/M4 约定 `input/`、`analysis/`、`candidates/{id}/` 和固定 `final/shader.frag|render.png|metrics.json|manifest.json` 布局；布局由 Agent 编排层决定，Store 仍只提供安全原子 I/O。

## 边界

- 默认产品路径由调用方配置为 `output/png-to-shader/`，核心类不读取环境变量；
- `register_run()` 是需要建立全局 run-id 映射时的创建入口；`start_run()` 只按已知 project/run 创建或恢复目录，不读取索引、也不提供跨项目碰撞保证。当前本地索引没有进程间锁，多进程调用方必须在上层串行化，不能把该保证解释为分布式唯一性。
- Artifact Store 不决定候选是否晋级，不写 LangGraph checkpoint，不调用模型；
- V1 不实现 S3、数据库索引、回收策略或跨机器共享；
- API 层当前只按 `final-render`、`metrics`、`manifest` 三个白名单名字暴露产物，不能接受任意本地路径；`final/shader.frag` 只供内部证据校验。

## V2 Artifact Catalog

V2.0 在 V1 `RunArtifactStore` 之上增加路径无关的 Artifact 领域契约：

- `ArtifactRefV2` 只包含 opaque `artifact_id`、SHA-256、kind、schema version、content type 和字节数，不包含相对路径、本地绝对路径或对象 URI；领域 State、Candidate 和 Evidence 只能保存这类引用。
- `LocalArtifactCatalog` 构造时绑定一个 `run_id` 和对应 `RunArtifactStore`；`put()` 必须再次提交同一 `run_id`，避免 Catalog 被误用于其他运行。
- `list_refs()` / `total_size_bytes()` 提供经过 manifest 身份校验的 run 级快照，供可恢复 bootstrap 在“Catalog 已提交、外层 journal 尚未提交”的崩溃窗口内重新对账；字节数按内容寻址条目去重，不把私有 manifest/lock 计作领域 Artifact。
- 本地后端把内容写入 run 内部的私有 blob 布局，再以 `fsync` 和原子替换更新 run 级 manifest。artifact id 由运行、内容摘要和语义元数据的 canonical identity 稳定派生，但调用方不能从 id 推导物理路径。
- manifest 是本地后端的私有映射，不属于领域 Ref。解析条目时会按当前 run 和完整引用元数据重算 artifact id；每次读取还会重新验证 bytes 的长度和 SHA-256。缺失、元数据或内容篡改、manifest run 绑定不一致一律拒绝。
- `LegacyArtifactRefAdapter` 只读校验 V1 `ArtifactRef` 与原文件 bytes，并在内存中提供 opaque V2 Resolver 视图；它不实现 `put()`，也不复制或迁移 V1 文件。需要持久迁移时必须由后续显式迁移流程完成。

V2.0 仍是单机本地实现。文件锁只保护同一文件系统上的 manifest read-modify-write，不提供分布式事务、跨机器共享、回收或 V5 Blob/Binding 语义；V5 可替换 Catalog/Blob 后端，但不得改变 `ArtifactRefV2` 领域形状。
