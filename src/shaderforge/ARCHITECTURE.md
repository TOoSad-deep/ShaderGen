# ShaderForge 架构

ShaderForge 是不依赖 FastAPI、LangChain 或 React 的确定性领域核心。当前 `scene_mvp` 以 ShaderGraph 为产品表示，同时保留 MinScene 感知 seed 与兼容路径：

- `contracts/`：通用 WebGL1 静态无贴图契约。
- `dsl/`：有序 Layer、层内受限 CSG、确定性哈希、参数清单和 specialized WebGL1 Compiler。
- `scene/`：严格 MinScene 和 typed Patch，仅供感知 seed 与 legacy 兼容。
- `perception/`：参考图确定性感知与 fallback MinScene，产品 Author 前转换为 ShaderDocument。
- `generation/`：固定 WebGL1 模板、typed uniform 和 baked GLSL。
- `rendering/`：Playwright/Chromium WebGL1、prepared uniform 热渲染和 run-scoped 有界多 program registry。
- `evaluation/`：`min_scene_composite_v3` 与空间残差。
- `optimization/`：有界 base/feature/patch 候选。
- `validation/`：静态 WebGL1 校验。
- `store/`：run Artifact。

`shaderforge.public` 只聚合最小骨架的跨域稳定类型；需要聚焦依赖时从 typed 子包公共根导入。Agent/Backend 不得直接导入子包私有实现。

旧 V1 TargetMeasurements、Basic Oracle、current_best Selector、measurement-affine seed、V1 业务契约和 benchmark 包已删除。通用 WebGL1 合同里的版本后缀表示合同版本，不代表旧 V1 产品路径。
