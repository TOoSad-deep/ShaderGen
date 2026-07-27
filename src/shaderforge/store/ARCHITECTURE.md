# ShaderForge Store 架构

`store/` 保存 ShaderForge 运行的大产物和可复现证据。它与 Agent Memory 分离：Memory 只保存精炼摘要和 artifact id/hash，不复制图片、GLSL 或完整评分。

## 当前能力

当前只实现 `LocalArtifactStore`；M3 在不扩张 Store 职责的前提下用它保存完整 run/candidate 证据：

- 按 `project_id/run_id` 隔离运行目录；
- 写入 bytes、UTF-8 文本和稳定 JSON；
- 临时文件、`fsync`、原子 `os.replace`；
- 可选 `restrictive_permissions=True` 供默认产品 store 与 rollout 私有 store 使用：
  base/project/run/`.run-index`/嵌套目录强制 0700，每次写入后的普通文件强制
  0600；核心类默认仍为关闭，测试或独立调用方必须显式选择；
- 返回相对路径、SHA-256、字节数和 content type；
- 拒绝绝对路径、`..`、非法标识和 symlink 逃逸。
- `register_run()` 用内部 `.run-index/{run_id}.json` 持久映射 project/run，并在单进程顺序调用范围内拒绝同一 run_id 跨项目碰撞；客户端不能控制索引内容。
- M3/M4 约定 `input/`、`analysis/`、`candidates/{id}/` 和固定 `final/shader.frag|render.png|metrics.json|manifest.json` 布局；布局由 Agent 编排层决定，Store 仍只提供安全原子 I/O。
- rollout 父 run 的三个公开 final 文件先逐文件 flush/fsync，再 fsync staging
  目录、以 rename 发布并 fsync 父目录；复验通过 pinned directory fd、
  `O_NOFOLLOW`、普通文件 `fstat` 和目录项 inode/mtime/ctime 快照读取，拒绝
  文件/目录 symlink 以及读取期间替换。该本地 API 仍要求调用方独占 Artifact 根；
  它不是对恶意同权限并发写者提供的事务文件系统。

## 边界

- 默认产品路径由调用方配置为 `output/png-to-shader/`，核心类不读取环境变量；
- `register_run()` 是需要建立全局 run-id 映射时的创建入口；`start_run()` 只按已知 project/run 创建或恢复目录，不读取索引、也不提供跨项目碰撞保证。当前本地索引没有进程间锁，多进程调用方必须在上层串行化，不能把该保证解释为分布式唯一性。
- Artifact Store 不决定候选是否晋级，不写 LangGraph checkpoint，不调用模型；
- V1 不实现 S3、数据库索引、回收策略或跨机器共享；
- API 层当前只按 `final-render`、`metrics`、`manifest` 三个白名单名字暴露产物，不能接受任意本地路径；`final/shader.frag` 只供内部证据校验。
