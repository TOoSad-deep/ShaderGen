# Generation 架构

当前只保留 `scene_mvp` 固定模板：

- 严格 MinScene 物化为 WebGL1 source、uniform schema 和值；
- prepared 路径热更新 uniform；
- final Artifact 烘焙为自包含 GLSL；
- 最多四个 feature 固定槽位，并受 WebGL1 fragment uniform 上限约束。

Generation 不读取 benchmark、case id、数据库或模型输出原文。
