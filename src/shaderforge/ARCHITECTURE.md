# ShaderForge 架构

ShaderForge 是不依赖 FastAPI、LangChain 或 React 的确定性领域核心。当前只保留 `scene_mvp` 所需能力：

- `contracts/`：通用 WebGL1 静态无贴图契约。
- `scene/`：严格 MinScene 和 typed Patch。
- `perception/`：参考图确定性感知与 fallback Scene。
- `generation/`：固定 WebGL1 模板、typed uniform 和 baked GLSL。
- `rendering/`：Playwright/Chromium WebGL1 与 prepared uniform 热渲染。
- `evaluation/`：`min_scene_composite_v3` 与空间残差。
- `optimization/`：有界 base/feature/patch 候选。
- `validation/`：静态 WebGL1 校验。
- `store/`：run Artifact。

`shaderforge.public` 只聚合最小骨架的跨域稳定类型；需要聚焦依赖时从 typed 子包公共根导入。Agent/Backend 不得直接导入子包私有实现。

旧 V1 TargetMeasurements、Basic Oracle、current_best Selector、measurement-affine seed、V1 业务契约和 benchmark 包已删除。通用 WebGL1 合同里的版本后缀表示合同版本，不代表旧 V1 产品路径。
