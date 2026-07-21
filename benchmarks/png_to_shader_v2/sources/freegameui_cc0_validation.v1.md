# FreeGameUI CC0 验证集来源记录（v1）

## 范围

本记录覆盖 `dataset_manifest.v1.json` 的 `freegameui_cc0_validation_v1`：30 张仅供可见 `validation` 使用的 PNG。下载日期为 2026-07-17，所有本地文件均为 512×512；其内容 SHA-256 是 Manifest 中唯一有效的内容身份。

- 素材站：[Free Game UI Assets](https://freegameui.net/)
- 目录：[Gauges](https://freegameui.net/assets/gauges/) 与 [Shapes](https://freegameui.net/assets/shapes/)
- 许可证：CC0 1.0（页面的 `ImageObject.license` 指向 <https://creativecommons.org/publicdomain/zero/1.0/>）
- 署名记录：`Yu-Rin-Chi Game Studio`；CC0 不要求署名，但保留来源链以支持审计。

单个详情页将原始 SVG、512 px 预览 PNG、许可证和创作者放在 JSON-LD 中；例如：[blue-gold ring](https://freegameui.net/assets/gauges/gauge_ring_blue-gold_01/) 与 [rounded rectangle ring](https://freegameui.net/assets/shapes/sh_rect_ring-rounded_01/)。其他样本的详情页按同一稳定路径取得：`https://freegameui.net/assets/{gauges|shapes}/{asset-name}/`，PNG 路径及其 SHA-256 以 Manifest 为准。

## 标签分组

| Manifest 分组 | 数量 | 关键正例 | 标注原则 |
| --- | ---: | --- | --- |
| `freegameui_ring_gauge` | 10 | ring、highlight、rim、outline | 连续环带、内外边界与可见高光；记录 `radial_band`、`outline_band`、`arc_highlight`。 |
| `freegameui_segmented_ring_gauge` | 10 | multi_instance、ring、rim、outline | 12 个可分离的环段；记录组合与叠加 primitive。 |
| `freegameui_hollow_shape` | 10 | hollow、outline | 各有一个内孔。复杂的心形/多边形外轮廓不伪标为当前 registry 没有的几何 primitive，只记录可由当前 V2.0 registry 验证的 `difference_mask` 和 `outline_band`。 |

为防止变体泄漏，三个来源视觉族分别使用完整的 `hash_group`，且均只出现在 `validation`，不会进入 `development` 或 `release-held-out`。

## 边界

这批素材由开发侧可见并完成标签，因此不能充当独立 release 证据。`release-held-out` 继续保持未填充和封存状态，必须在 V2.3 冻结后由独立数据保管人选取、下载和标注。
