# 中等与困难验证素材来源记录（v1）

## 范围

本记录覆盖 `dataset_manifest.v1.json` 于 2026-07-19 新增的 11 个 visible validation 样本。图片内容身份只以 Manifest 中的 SHA-256 为准；所有变体均只出现在 validation，绝不进入 release-held-out。

## 中等：金属圆形按钮（6 张）

来源为 [FreeGameUI 的 blue-gold 圆形按钮详情页](https://freegameui.net/assets/buttons/btn_circle_blue-gold_01/) 和 [green-gold 圆形按钮详情页](https://freegameui.net/assets/buttons/btn_circle_green-gold_01/)。这些页以 JSON-LD 声明 CC0 1.0、创作者 `Yu-Rin-Chi Game Studio`、原始 SVG 和官方 512 px PNG。

纳入 `btn_circle_blue-gold_01..03`、`btn_circle_blue-silver_01..02` 及 `btn_circle_green-gold_01`。它们共有 `freegameui.buttons_circle_metallic` hash group，包含圆形 base fill、渐变、rim、outline 与弧形高光；这是中等层次，而不是六个独立泛化样本。

## 困难：爆炸、烟雾与 Glow（5 张）

原始来源为 OpenGameArt 的 [Explosion particles sprite atlas](https://opengameart.org/content/explosion-particles-sprite-atlas)。页面将 `explosion_atlas_512x512.png` 标为 CC0，并说明其基于 Kenney 的 Smoke Particles；原作者署名虽非强制仍保留为 `TheJosh / Kenney`。

- `explosion_atlas_512x512.png`：官方 512×512、3×3 的九实例爆炸图集。
- `oga_explosion_cell_00`、`01`、`10`、`22`：从上述原件按像素网格无插值裁切的 171×171 PNG，裁切原点依次为 `(0,0)`、`(171,0)`、`(0,171)`、`(342,342)`，大小均为 `(171,171)`。

这五项共享 `oga.explosion_atlas_512` hash group。其结构标签只断言当前 V2.0 taxonomy 可以验证的 color lobe、glow 与叠加；烟雾的有机噪声边界和精确粒子形态没有可用的 Genome primitive，故不伪称 Compiler 已能精确表达它们。

## 难度边界

难度等级只作为本来源记录的人工策展说明，尚未写入 strict Manifest schema：

| 层次 | 数量 | 主要难点 |
| --- | ---: | --- |
| 中等 | 6 | 渐变、rim/outline、高光与局部暗部的相对层次。 |
| 困难 | 5 | 多实例、半透明烟雾、色团、Glow、叠加与不规则边界。 |

它们提高可见 validation 的难度覆盖，不构成 sealed release 或 production admission 的证据。
