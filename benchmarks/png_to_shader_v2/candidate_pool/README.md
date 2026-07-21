# PNG-to-Shader V2 CC0 候选池

本目录保存 2026-07-20 由开发侧公开检索并下载的 CC0 素材，供后续数据筛选、结构标签设计和非发布诊断使用。

## 当前状态

- 访问级别：`development_visible_candidate`
- PNG 文件：974 个，内容哈希唯一值 894 个
- 包内重复：20 组、80 个重复文件；选择样本前必须按 SHA-256 去重
- 图片完整性：974/974 可由 Pillow 解码
- 与当前 development/validation 样本的 SHA-256 碰撞：0
- `release-held-out` 资格：否。开发侧已经看见这些文件，不能把它们登记为原始 sealed release 证据

机器可读来源与快照信息见 `inventory.v1.json`，抽样总览见 `overview.png`。

## 目录

- `raw_archives/`：官方下载包原件
- `images/kenney_particle_pack/`：Kenney Particle Pack 的透明 PNG 与包内许可证
- `images/kenney_ui_pack/`：Kenney UI Pack 的 PNG 与包内许可证
- `images/opengameart/`：OpenGameArt 单图以及两个小型包的解包结果

## 使用边界

1. 这些素材不能直接写入 `dataset_manifest.v1.json` 的 `release-held-out`。
2. 若选入 development/validation 的新版本，必须先按内容哈希去重，再冻结 `visual_family`、`hash_group`、拓扑、实例数、孔洞数、required layers 与 expected primitives。
3. Ring 动画帧、色彩变体和 UI Pack 的 Default/Double 变体属于同一来源视觉族，不得拆到不同 split。
4. Kenney 两个包内的 `License.txt` 是随包许可证证据；OpenGameArt 来源页是对应单图/包的许可证据。
5. 原始 sealed release 仍应由独立数据保管人在 V2.3 冻结后从未暴露给开发侧的来源中选取。

