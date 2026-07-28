# 图片复刻无贴图 Shader 方法论与技术细节

> 归档状态：领域方法论参考，不定义当前架构、验证基线、benchmark 或待办。

## 1. 文档目的

本文总结如何把一张静态参考图拆解为可测量、可编程、可验证的纯程序化 Fragment Shader，并以粉色玻璃圆片效果为完整案例。

目标不是针对单张图片“凭感觉写一段 GLSL”，而是建立一套可以重复使用的工程流程：

1. 先确认运行时契约；
2. 再把图片拆成独立视觉层；
3. 用像素坐标量化几何、颜色、高光和阴影；
4. 用 SDF、Gaussian、渐变和解析遮罩逐层重建；
5. 在真实 WebGL 中编译和渲染；
6. 组合 bbox、代表性像素、RMSE 和人工视觉复核进行有限迭代；
7. 保留最终 Shader、渲染图和验证证据，清理临时工具。

本文对应的案例产物：

- 参考图：`/Users/douwen/Desktop/p2s-test/参考图.png`
- 最终 Shader：`output/static_pink_glass_orb.glsl`
- 最终渲染图：`output/static_pink_glass_orb.png`
- 设计规格：`docs/superpowers/specs/2026-07-10-static-pink-glass-orb-shader-design.md`
- 实施报告：`.superpowers/sdd/task-2-report.md`

## 2. 核心方法论

### 2.1 从“识别物体”切换为“识别视觉层”

自然语言通常会把参考图描述成“一个粉色、透明、有高光的圆”。这种描述适合沟通，但不足以直接生成可调的 Shader。

程序化重建需要把它转换成如下结构：

```text
最终颜色
  = 白色背景
  + 右侧外晕
  + 底部柔影
  + 椭圆主体遮罩
    × (
        主方向渐变
        + 左侧暗色团
        + 右侧亮色团
        + 底部乳白色团
        + 内部雾化
        + 宽 rim
        + 窄描边
        + 内侧细亮边
        + 左上高光
        + 右下高光
        + 顶部细亮边
      )
```

这个拆法有两个直接收益：

- 每个视觉问题都能映射到一组独立参数；
- 每轮调参只改一个问题域，便于判断指标变化的原因。

例如：

| 视觉问题 | 首选参数 |
|---|---|
| 圆片位置不对 | `center` |
| 圆片大小不对 | `radius` |
| 轮廓太硬 | `aa`、`softRim` |
| 左侧不够深 | `deepPink`、`darkLobe` |
| 底部不够白 | `milkLobe` |
| 主体像平面渐变 | `innerHaze`、局部色团 |
| 玻璃厚度不足 | `softRim`、`innerSheen`、`outerStroke` |
| 左上高光太长 | 高光切向 `sigma.x` |
| 高光太宽 | 高光法向 `sigma.y` 或 radial sigma |
| 右下高光不够白 | `rightCore` 的 radial 目标和混合强度 |
| 阴影落到画布底边 | 阴影中心、纵向 sigma、混合强度 |

### 2.2 优先采用“直接拟合”，而不是“物理正确”

本案例评估过三种实现路线。

#### 路线 A：椭圆 SDF 分层合成

- 用椭圆 SDF 定义主体；
- 用方向渐变和 Gaussian 拟合颜色；
- 用 radial band 生成 rim；
- 用方向 Gaussian 与径向 Gaussian 的乘积生成弧形高光；
- 用偏移 Gaussian 生成阴影。

优点：参数与参考图测量值一一对应，容易快速收敛。

#### 路线 B：伪球面法线光照

- 从圆内坐标重建球面法线；
- 计算 diffuse、Fresnel 和 specular；
- 再叠加颜色渐变和阴影。

优点：立体感更自然。缺点：参考图中的高光是风格化弯月，不完全遵循简单球面光照，仍要增加额外遮罩。

#### 路线 C：Bezier 或精确曲线 SDF

- 用二次 Bezier、偏心椭圆弧或曲线距离场描述高光；
- 可配合多重采样提高质量。

优点：曲线控制最精确。缺点：数学、调参和像素成本最高。

本案例选择路线 A。原因是目标是复刻一张固定图，而不是建立通用透明材质。直接拟合比物理建模更短、更稳定，也更容易通过像素指标优化。

### 2.3 先对齐“大结构”，再优化“小细节”

推荐的调参顺序：

1. 画布和坐标方向；
2. 主体中心、半径和 bbox；
3. 阴影位置、宽度和尾部；
4. 主方向渐变；
5. 局部色团；
6. rim 和描边；
7. 左右高光；
8. 内部雾化和细亮边；
9. 最后调整局部 RGB。

如果主体几何尚未对齐就开始调高光颜色，后续移动圆心或半径时会使高光位置整体变化，之前的局部优化会失效。

## 3. 第一步：锁定运行时契约

### 3.1 当前 ShaderGen 前端契约

当前前端使用 WebGL1 / GLSL ES 1.00，Fragment Shader 必须满足：

```glsl
precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
  gl_FragColor = vec4(...);
}
```

本案例的额外约束：

- 禁止 `#version`；
- 禁止 WebGL2 的 `in`、`out`、`texture()` 和自定义输出变量；
- `u_image` 必须声明以兼容现有前端，但不能调用 `texture2D`；
- `u_time` 必须声明，但不能参与任何表达式；
- 输出 alpha 固定为 `1.0`；
- 画面完全静态；
- 不使用噪声、循环、多重采样或外部依赖。

### 3.2 为什么保留未使用的 uniform

前端会尝试查询 `u_image`、`u_resolution` 和 `u_time`。如果某个 uniform 未参与 Shader 计算，GLSL 编译器可能将其优化掉，`getUniformLocation()` 会返回 `null`。

WebGL 对 `null` uniform location 的写入是安全的无操作，因此可以保留接口声明而不实际使用贴图和时间。这样既遵守现有前端契约，又满足纯程序化要求。

### 3.3 不要混用不同平台的入口

常见平台差异：

| 平台 | 常见入口或输出 |
|---|---|
| WebGL1 | `void main()`、`gl_FragColor` |
| WebGL2 | `#version 300 es`、`out vec4 fragColor` |
| ShaderToy | `mainImage(out vec4, in vec2)`、`iResolution` |
| Unity | ShaderLab/HLSL 包装、`SV_Target` |
| Godot | `shader_type canvas_item`、`COLOR` |

复刻前必须先确定目标运行时，否则视觉公式正确也可能无法编译。

## 4. 第二步：量化参考图

### 4.1 几何测量

本案例测得：

| 项目 | 数值 |
|---|---:|
| 画布 | 505 × 527 |
| 背景 | 约 `#FEFEFE` |
| 主体外框 | `x=38..465`、`y=32..454` |
| 主体中心 | `(251.5, 243.3)` |
| X 半径 | `213.5` |
| Y 半径 | `211.5` |
| 归一化中心 | 约 `(0.498, 0.462)` |

虽然肉眼看起来是圆，但 X/Y 半径仍有轻微差异。使用椭圆模型可以避免把误差强行分配给抗锯齿或描边。

### 4.2 颜色测量

主体颜色大致沿左上到右下变亮：

| 区域 | 近似颜色 |
|---|---|
| 左上/左侧 | `#F0003B` 到 `#FF1C5C` |
| 中部 | `#FF70A0` 到 `#FF9ABD` |
| 底部 | `#FFD8E5` 到 `#FFF7FA` |

仅使用两端颜色做线性插值会产生平面感，因此要继续识别局部色团：

- 左侧暗红 Gaussian；
- 右侧亮粉 Gaussian；
- 底部乳白 Gaussian；
- 中下部轻微暖色雾化。

### 4.3 高光测量

左上高光：

- 中心约 `(153, 91)`；
- 长度约 `105–120 px`；
- 宽度约 `12–25 px`；
- 轴向约 `-35°`；
- 形状是贴近圆周的弯月，而不是直线胶囊；
- 外层粉白软晕比白色核心更宽。

右下高光：

- 中心约 `(368, 377)`；
- 长度约 `110–130 px`；
- 宽度约 `15–25 px`；
- 轴向约 `-43°`；
- 比左上高光更宽、更柔；
- 中心接近纯白。

### 4.4 阴影测量

阴影不是一个统一的大模糊圆，而是两部分：

- 右侧外晕：窄、纵向延伸；
- 底部柔影：宽、横向延伸。

如果只用一个大 Gaussian，常见结果是阴影从主体底部一直延伸到画布边缘，颜色过浓且方向不可控。

### 4.5 选择代表性像素

为了避免只看整图平均误差，选择七个具有明确语义的位置：

```text
background      白色背景
deep_left       左侧深粉
center          主体中心
bottom_milk     底部乳白
left_highlight  左上高光
right_highlight 右下高光
shadow          底部阴影
```

代表性像素的用途是定位问题。例如：

- `center.G` 偏低：优先调整中间粉色或 haze，不要动圆心；
- `right_highlight` 不够白：调整右高光核心，不要整体提高曝光；
- `shadow.G` 偏低：阴影太饱和或太深；
- `bottom_milk` 已经准确：后续调色时应保护该区域。

## 5. 第三步：统一坐标系统

### 5.1 翻转 Y 轴

WebGL 的 `v_uv.y=0` 位于底部，而图片测量通常以左上角为原点，所以先翻转 Y：

```glsl
vec2 uv = vec2(v_uv.x, 1.0 - v_uv.y);
```

### 5.2 映射到固定参考域

```glsl
vec2 referenceSize = vec2(505.0, 527.0);
vec2 pixel = uv * referenceSize;
```

所有测量值都直接在 505×527 参考坐标中表达。优点是：

- 中心 `(251.5, 243.3)` 可以直接写入；
- 高光和阴影中心也能直接使用测量值；
- 当前画布改变时仍保持相对布局；
- 调参时不需要不断在像素和 UV 之间手算。

准确复刻仍应优先使用与参考图一致或接近的宽高比。宽高比发生明显变化时，虽然布局比例不变，但视觉会随画布拉伸。

### 5.3 `u_resolution` 只负责抗锯齿尺度

```glsl
vec2 safeResolution = max(u_resolution, vec2(1.0));
float referencePixel = 0.5 * (
  referenceSize.x / safeResolution.x +
  referenceSize.y / safeResolution.y
);
```

`referencePixel` 表示一个实际输出像素大约对应多少参考坐标单位。这样可以让轮廓抗锯齿在不同分辨率下仍接近 1–2 个实际像素。

## 6. 第四步：椭圆 SDF 和抗锯齿

### 6.1 椭圆局部坐标

```glsl
vec2 ellipse = (pixel - center) / radius;
float radial = length(ellipse);
```

其几何含义：

```text
radial < 1.0  主体内部
radial = 1.0  理论边界
radial > 1.0  主体外部
```

这里的 `radial` 不是严格的欧氏距离场，但对椭圆遮罩、rim 和局部径向带已经足够，而且计算量很低。

### 6.2 抗锯齿遮罩

```glsl
float aa = 1.2 * referencePixel / min(radius.x, radius.y);
float bodyMask = 1.0 - smoothstep(
  1.0 - aa,
  1.0 + aa,
  radial
);
```

`smoothstep` 的过渡区横跨边界两侧。`aa` 越大，轮廓越软；越小，轮廓越锐，但更容易出现锯齿。

不要把 `aa` 固定为某个归一化常量，否则更换输出分辨率后边缘像素宽度会变化。

## 7. 第五步：Gaussian 基础工具

### 7.1 数学定义

二维各向异性 Gaussian：

\[
G(p;c,\sigma)=\exp\left(-\frac{1}{2}\left\|\frac{p-c}{\sigma}\right\|^2\right)
\]

对应 GLSL：

```glsl
float gaussian(vec2 point, vec2 center, vec2 sigma) {
  vec2 q = (point - center) / sigma;
  return exp(-0.5 * dot(q, q));
}
```

参数含义：

- `center`：色团或阴影中心；
- `sigma.x`：横向宽度；
- `sigma.y`：纵向宽度；
- 外部乘数：强度；
- 后续 `mix` 的目标颜色：色相和亮度。

### 7.2 为什么 Gaussian 适合本图

参考图的主体、阴影、高光和内部雾化都具备以下特征：

- 连续；
- 无颗粒；
- 局部集中；
- 边缘平滑衰减。

因此 Gaussian 比噪声、Voronoi 或复杂纹理函数更合适。噪声会引入参考图没有的高频信息。

### 7.3 `mix` 的语义

```glsl
body = mix(body, targetColor, weight);
```

等价于：

\[
body_{new}=body_{old}(1-weight)+targetColor\cdot weight
\]

因此 Gaussian 通常不直接加到 RGB，而是作为混合权重。直接相加容易过曝或改变 alpha 语义。

## 8. 第六步：分层构造颜色

### 8.1 背景与阴影

```glsl
vec3 background = vec3(0.996);

float shadow =
  0.20 * gaussian(pixel, vec2(455.0, 290.0), vec2(29.0, 80.0)) +
  0.22 * gaussian(pixel, vec2(290.0, 462.0), vec2(90.0, 32.0));

vec3 color = mix(
  background,
  vec3(0.96, 0.32, 0.55),
  clamp(shadow * (1.0 - bodyMask), 0.0, 0.32)
);
```

注释：

- 第一组 Gaussian 较窄且纵向延伸，负责右侧外晕；
- 第二组较宽且横向延伸，负责底部柔影；
- `1.0 - bodyMask` 阻止阴影污染主体内部；
- `clamp(..., 0.0, 0.32)` 限制最大不透明度，避免阴影变成实色粉块。

### 8.2 主方向渐变

```glsl
float gradient = clamp(
  0.58 + 0.18 * ellipse.x + 0.35 * ellipse.y,
  0.0,
  1.0
);
```

解释：

- 常量 `0.58` 决定主体中心的基础亮度；
- `ellipse.x` 权重为 `0.18`，表示向右逐渐变亮；
- `ellipse.y` 权重为 `0.35`，表示向下变亮更明显；
- Y 权重大于 X，因此整体方向主要是上深下浅。

三段颜色：

```glsl
vec3 deepPink = vec3(0.97, 0.02, 0.22);
vec3 hotPink  = vec3(1.00, 0.47, 0.65);
vec3 palePink = vec3(1.00, 0.96, 0.98);

vec3 body = mix(
  deepPink,
  hotPink,
  smoothstep(0.05, 0.68, gradient)
);

body = mix(
  body,
  palePink,
  smoothstep(0.48, 1.02, gradient)
);
```

为什么分两次 `mix`：

- 直接从深粉插值到粉白会丢失中间的鲜艳粉色；
- 两段插值可以独立控制“深粉到亮粉”和“亮粉到乳白”的转折区；
- 两段区间有重叠，可以得到更柔和的色阶。

### 8.3 局部色团

```glsl
float darkLobe  = gaussian(pixel, vec2(78.0, 178.0), vec2(92.0, 132.0));
float rightLobe = gaussian(pixel, vec2(405.0, 250.0), vec2(175.0, 175.0));
float milkLobe  = gaussian(pixel, vec2(252.0, 423.0), vec2(178.0, 78.0));
```

三个色团分别解决：

- 左侧不够红、不够深；
- 右侧中部过于均匀；
- 底部没有参考图中的乳白透光区。

### 8.4 内部雾化

```glsl
float innerHaze =
  gaussian(pixel, vec2(270.0, 330.0), vec2(165.0, 135.0)) *
  (1.0 - smoothstep(0.70, 0.96, radial));

body = mix(
  body,
  vec3(1.0, 0.80, 0.70),
  0.09 * innerHaze
);
```

关键点：

- haze 中心放在中下部，而不是几何中心；
- Gaussian 负责空间分布；
- radial gate 让 haze 在接近边缘时逐渐消失，避免破坏 rim；
- 权重只有 `0.09`，它应该被感觉到，而不是被直接看到。

如果 haze 权重过大，主体会变成一块浑浊的浅粉；过小则仍像平面渐变。

## 9. 第七步：rim、描边和玻璃厚度

### 9.1 宽 rim

```glsl
float softRim = smoothstep(0.79, 0.995, radial);
```

从主体内部约 79% 半径开始逐渐增强，用于制造较宽的厚度区。

### 9.2 窄外描边

```glsl
float outerStroke = smoothstep(0.958, 0.993, radial);
```

只在靠近边界的窄区间出现，主要负责 3–5 px 的粉红外轮廓。

### 9.3 方向性边缘颜色

```glsl
float edgeLight = clamp(
  0.55 + 0.25 * ellipse.x + 0.40 * ellipse.y,
  0.0,
  1.0
);

vec3 edgeColor = mix(
  vec3(0.91, 0.02, 0.21),
  vec3(1.00, 0.63, 0.77),
  edgeLight
);
```

左上边缘偏深红，右下边缘偏浅粉。这样可以模拟玻璃边缘厚度受到主光方向影响，而不是得到一个机械均匀的圆环。

### 9.4 内侧细亮边

```glsl
float innerSheen =
  smoothstep(0.84, 0.89, radial) *
  (1.0 - smoothstep(0.92, 0.965, radial));
```

这是两个平滑阶跃相乘形成的有限径向带：

- 第一个 `smoothstep` 让亮边从内侧开始出现；
- 第二个反向 `smoothstep` 让亮边在外侧消失；
- 相乘后只保留中间一圈。

方向偏置：

```glsl
float sheenWeight =
  0.20 * innerSheen * (0.30 + 0.70 * edgeLight);
```

`0.30` 保证整圈存在很弱的玻璃层次，`0.70 * edgeLight` 让右下更亮。

## 10. 第八步：弧形高光

### 10.1 为什么旋转椭圆还不够

如果只放置一个旋转 Gaussian，得到的是直线胶囊状高光。参考图高光沿圆周弯曲，所以还需要径向约束。

最终高光结构：

\[
H(p)=G_{axis}(p)\cdot G_{radial}(p)
\]

其中：

- `G_axis` 控制局部位置、长度、宽度和方向；
- `G_radial` 控制高光贴在圆周的哪个半径上；
- 两者相乘后得到局部弧带。

### 10.2 构造旋转坐标

```glsl
vec2 axisCoordinates(vec2 point, vec2 center, vec2 axis) {
  vec2 delta = point - center;
  return vec2(
    dot(delta, axis),
    dot(delta, vec2(-axis.y, axis.x))
  );
}
```

返回：

- X：沿高光切线方向的距离；
- Y：垂直于高光方向的距离。

高光方向：

```glsl
vec2 highlightAxis = vec2(0.819, -0.574);
```

它近似单位向量，对应屏幕坐标约 `-35°`。

### 10.3 左上高光

```glsl
vec2 left = axisCoordinates(
  pixel,
  vec2(153.0, 87.0),
  highlightAxis
);

vec2 leftGlowDistance = left / vec2(70.0, 32.0);
vec2 leftCoreDistance = left / vec2(48.0, 15.0);

float leftGlowRadial = (radial - 0.88) / 0.065;
float leftCoreRadial = (radial - 0.885) / 0.027;
```

参数解释：

- `70`、`48` 控制软晕和核心的切向长度；
- `32`、`15` 控制软晕和核心的法向宽度；
- radial target `0.88`/`0.885` 控制高光离外边缘的距离；
- radial sigma `0.065`/`0.027` 控制弧带厚度；
- 核心比软晕更短、更窄、更接近纯白。

### 10.4 右下高光

右下高光采用相同结构，但：

- 切向和法向 sigma 更大；
- radial target 更靠内；
- 核心混合强度接近 1；
- 因而得到更宽、更柔、中心更白的高光。

### 10.5 调高光时各参数的直接影响

| 参数 | 增大后的效果 |
|---|---|
| 中心 X/Y | 整体移动高光 |
| 切向 sigma | 高光变长 |
| 法向 sigma | 高光变宽 |
| radial target | 高光沿半径向外移动 |
| radial sigma | 弧带变厚 |
| glow 权重 | 外层软晕更明显 |
| core 权重 | 白色核心更强 |

高光太长时，优先缩小切向 sigma，不要先降低整体强度。降低强度只会让一条过长的高光变淡，而不会修复形状。

## 11. WebGL1 `mediump` 数值安全

### 11.1 大数先平方可能溢出

不安全写法：

```glsl
left.x * left.x / (82.0 * 82.0)
```

在最低规格 `mediump` 实现中，`left.x` 可能超过 128，先平方可能超出保证范围。

安全写法：

```glsl
vec2 normalized = left / vec2(82.0, 38.0);
float squaredDistance = dot(normalized, normalized);
```

先把坐标缩放到较小范围，再平方。

### 11.2 小数先平方可能下溢

不安全写法：

```glsl
(radial - target) * (radial - target) / (sigma * sigma)
```

在 212 px 左右的半径下，一个像素对应的 radial 差约为 `0.0047`，平方约为 `2.2e-5`。最低规格 `mediump` 可能把这个中间值冲为 0，导致高光峰顶形成数像素的平台。

安全写法：

```glsl
float normalized = (radial - target) / sigma;
float squaredDistance = normalized * normalized;
```

### 11.3 通用规则

在 WebGL1 `mediump` 中：

> 先归一化，再平方、点积、开方或进入指数函数。

尽量让进入非线性运算的中间量处于约 `[-10, 10]` 范围。

本案例对五处 radial Gaussian 都执行了这一修复：

- `leftGlowRadial`
- `leftCoreRadial`
- `rightGlowRadial`
- `rightCoreRadial`
- `topRimRadial`

## 12. 最终 Shader 中文注释版

下面代码与最终实现的结构一致，注释用于说明每一层的作用。实际运行文件仍以 `output/static_pink_glass_orb.glsl` 为准。

```glsl
precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_image; // 仅兼容现有前端，不进行采样
uniform vec2 u_resolution; // 只用于抗锯齿像素尺度
uniform float u_time;      // 仅兼容现有前端，不参与计算

// 二维各向异性 Gaussian：center 控制位置，sigma 控制 X/Y 宽度。
float gaussian(vec2 point, vec2 center, vec2 sigma) {
  vec2 q = (point - center) / sigma;
  return exp(-0.5 * dot(q, q));
}

// 把屏幕坐标投影到“高光切线/高光法线”坐标系。
vec2 axisCoordinates(vec2 point, vec2 center, vec2 axis) {
  vec2 delta = point - center;
  return vec2(dot(delta, axis), dot(delta, vec2(-axis.y, axis.x)));
}

void main() {
  // 参考图使用左上原点。WebGL UV 使用左下原点，因此先翻转 Y。
  vec2 referenceSize = vec2(505.0, 527.0);
  vec2 uv = vec2(v_uv.x, 1.0 - v_uv.y);
  vec2 pixel = uv * referenceSize;

  // 主体椭圆参数。
  vec2 center = vec2(251.5, 243.3);
  vec2 radius = vec2(213.5, 211.5);
  vec2 ellipse = (pixel - center) / radius;
  float radial = length(ellipse);

  // 把抗锯齿宽度换算为当前画布中的实际像素尺度。
  vec2 safeResolution = max(u_resolution, vec2(1.0));
  float referencePixel = 0.5 * (
    referenceSize.x / safeResolution.x +
    referenceSize.y / safeResolution.y
  );
  float aa = 1.2 * referencePixel / min(radius.x, radius.y);
  float bodyMask = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, radial);

  // 白色背景 + 右侧外晕 + 底部柔影。
  vec3 background = vec3(0.996);
  float shadow =
    0.20 * gaussian(pixel, vec2(455.0, 290.0), vec2(29.0, 80.0)) +
    0.22 * gaussian(pixel, vec2(290.0, 462.0), vec2(90.0, 32.0));
  vec3 color = mix(
    background,
    vec3(0.96, 0.32, 0.55),
    clamp(shadow * (1.0 - bodyMask), 0.0, 0.32)
  );

  // 左上深、右下浅的主方向渐变。
  float gradient = clamp(
    0.58 + 0.18 * ellipse.x + 0.35 * ellipse.y,
    0.0,
    1.0
  );
  vec3 deepPink = vec3(0.97, 0.02, 0.22);
  vec3 hotPink = vec3(1.0, 0.47, 0.65);
  vec3 palePink = vec3(1.0, 0.96, 0.98);
  vec3 body = mix(deepPink, hotPink, smoothstep(0.05, 0.68, gradient));
  body = mix(body, palePink, smoothstep(0.48, 1.02, gradient));

  // 三个局部色团：左侧加深、右侧提亮、底部乳白。
  float darkLobe = gaussian(pixel, vec2(78.0, 178.0), vec2(92.0, 132.0));
  float rightLobe = gaussian(pixel, vec2(405.0, 250.0), vec2(175.0, 175.0));
  float milkLobe = gaussian(pixel, vec2(252.0, 423.0), vec2(178.0, 78.0));
  body = mix(body, vec3(0.92, 0.02, 0.19), 0.22 * darkLobe);
  body = mix(body, vec3(1.0, 0.60, 0.75), 0.20 * rightLobe);
  body = mix(body, vec3(1.0, 0.975, 0.987), 0.68 * milkLobe);

  // 轻微内部雾化。radial gate 防止雾化覆盖边缘层次。
  float innerHaze =
    gaussian(pixel, vec2(270.0, 330.0), vec2(165.0, 135.0)) *
    (1.0 - smoothstep(0.70, 0.96, radial));
  body = mix(body, vec3(1.0, 0.80, 0.70), 0.09 * innerHaze);

  // 宽 rim、窄描边和方向性边缘颜色。
  float softRim = smoothstep(0.79, 0.995, radial);
  float outerStroke = smoothstep(0.958, 0.993, radial);
  float edgeLight = clamp(
    0.55 + 0.25 * ellipse.x + 0.40 * ellipse.y,
    0.0,
    1.0
  );
  vec3 edgeColor = mix(
    vec3(0.91, 0.02, 0.21),
    vec3(1.0, 0.63, 0.77),
    edgeLight
  );
  body = mix(
    body,
    edgeColor,
    clamp(0.24 * softRim + 0.58 * outerStroke, 0.0, 0.78)
  );

  // 两个 smoothstep 组成有限内侧亮环，并向右下方向增强。
  float innerSheen =
    smoothstep(0.84, 0.89, radial) *
    (1.0 - smoothstep(0.92, 0.965, radial));
  body = mix(
    body,
    vec3(1.0, 0.86, 0.94),
    clamp(0.20 * innerSheen * (0.30 + 0.70 * edgeLight), 0.0, 0.20)
  );

  // 左右高光共享近似 -35° 的切线方向。
  vec2 highlightAxis = vec2(0.819, -0.574);

  // 左上高光：较短、较窄，包含粉白软晕和白色核心。
  vec2 left = axisCoordinates(pixel, vec2(153.0, 87.0), highlightAxis);
  vec2 leftGlowDistance = left / vec2(70.0, 32.0);
  vec2 leftCoreDistance = left / vec2(48.0, 15.0);
  float leftGlowRadial = (radial - 0.88) / 0.065;
  float leftCoreRadial = (radial - 0.885) / 0.027;
  float leftGlow =
    exp(-0.5 * dot(leftGlowDistance, leftGlowDistance)) *
    exp(-0.5 * leftGlowRadial * leftGlowRadial);
  float leftCore =
    exp(-0.5 * dot(leftCoreDistance, leftCoreDistance)) *
    exp(-0.5 * leftCoreRadial * leftCoreRadial);
  body = mix(body, vec3(1.0, 0.72, 0.92), 0.42 * leftGlow);
  body = mix(body, vec3(1.0), clamp(0.94 * leftCore, 0.0, 0.96));

  // 右下高光：更宽、更柔，核心接近纯白。
  vec2 right = axisCoordinates(pixel, vec2(368.0, 382.0), highlightAxis);
  vec2 rightGlowDistance = right / vec2(88.0, 43.0);
  vec2 rightCoreDistance = right / vec2(65.0, 20.0);
  float rightGlowRadial = (radial - 0.86) / 0.075;
  float rightCoreRadial = (radial - 0.855) / 0.026;
  float rightGlow =
    exp(-0.5 * dot(rightGlowDistance, rightGlowDistance)) *
    exp(-0.5 * rightGlowRadial * rightGlowRadial);
  float rightCore =
    exp(-0.5 * dot(rightCoreDistance, rightCoreDistance)) *
    exp(-0.5 * rightCoreRadial * rightCoreRadial);
  body = mix(body, vec3(1.0, 0.93, 0.97), 0.48 * rightGlow);
  body = mix(body, vec3(1.0), clamp(0.99 * rightCore, 0.0, 0.995));

  // 顶部极细亮边。
  float topRimRadial = (radial - 0.985) / 0.018;
  float topRim =
    gaussian(pixel, vec2(252.0, 45.0), vec2(145.0, 25.0)) *
    exp(-0.5 * topRimRadial * topRimRadial);
  body = mix(body, vec3(1.0, 0.68, 0.80), 0.42 * topRim);

  // 主体覆盖背景和阴影，最终输出不透明颜色。
  color = mix(color, body, bodyMask);
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
```

## 13. 第九步：真实 WebGL1 验证

### 13.1 为什么静态扫描不够

文本扫描只能确认：

- 是否包含禁止语法；
- 是否声明必需 uniform；
- 是否存在入口和输出。

它不能证明：

- 浏览器驱动真的接受该 Shader；
- vertex/fragment interface 可以链接；
- `mediump` 表达式实际可执行；
- canvas 可以产生有效像素；
- 最终画面方向没有翻转。

所以必须在真实 WebGL1 context 中完成 compile、link 和 draw。

### 13.2 最小验证页结构

```js
const gl = canvas.getContext("webgl", {
  preserveDrawingBuffer: true,
});

const vertexShader = compile(
  gl,
  gl.VERTEX_SHADER,
  vertexSource,
);

const fragmentShader = compile(
  gl,
  gl.FRAGMENT_SHADER,
  fragmentSource,
);

const program = gl.createProgram();
gl.attachShader(program, vertexShader);
gl.attachShader(program, fragmentShader);
gl.linkProgram(program);

if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
  throw new Error(gl.getProgramInfoLog(program));
}

gl.viewport(0, 0, 505, 527);
gl.useProgram(program);
gl.uniform2f(
  gl.getUniformLocation(program, "u_resolution"),
  505,
  527,
);
gl.drawArrays(gl.TRIANGLES, 0, 6);

document.body.dataset.status = "ready";
```

### 13.3 启动本地服务器

```bash
python3 -m http.server 4178 --directory output
```

不建议直接使用 `file://`，因为浏览器对本地模块、fetch 和跨域行为可能有额外限制。

### 13.4 使用 Playwright 截图

```bash
PWCLI="$HOME/.codex/skills/playwright/scripts/playwright_cli.sh"

"$PWCLI" --session static-pink-orb \
  open http://127.0.0.1:4178/static_pink_glass_orb.preview.html

"$PWCLI" --session static-pink-orb \
  run-code "async (page) => {
    await page.waitForFunction(
      () => document.body.dataset.status !== 'loading'
    );
    if (
      await page.locator('body').getAttribute('data-status') !== 'ready'
    ) {
      throw new Error(await page.locator('pre').textContent());
    }
    await page.locator('canvas').screenshot({
      path: '/absolute/path/output/static_pink_glass_orb.png'
    });
  }"
```

完成后检查：

```bash
"$PWCLI" --session static-pink-orb console warning
sips -g pixelWidth -g pixelHeight output/static_pink_glass_orb.png
```

验收：

- 页面状态为 `ready`；
- 控制台 0 errors / 0 warnings；
- PNG 为 505×527；
- canvas 不是空图；
- 画面方向正确。

### 13.5 清理临时资源

最终产物不应依赖验证页。完成后：

1. 删除 `output/static_pink_glass_orb.preview.html`；
2. 关闭 Playwright session；
3. 停止 4178 静态服务器；
4. 最终只保留 GLSL 和 PNG。

## 14. 第十步：像素指标与视觉复核

### 14.1 可复用的对比脚本

```python
import numpy as np
from PIL import Image

reference = np.asarray(
    Image.open("/path/to/reference.png").convert("RGB"),
    dtype=np.float32,
)
rendered = np.asarray(
    Image.open("output/static_pink_glass_orb.png").convert("RGB"),
    dtype=np.float32,
)

assert reference.shape == rendered.shape


def pink_bbox(image: np.ndarray) -> tuple[int, int, int, int]:
    red = image[..., 0]
    green = image[..., 1]
    blue = image[..., 2]

    # 这是“粉色可见区域”启发式，不是通用主体分割算法。
    mask = ((red - green) > 15.0) & ((red - blue) > 4.0)
    ys, xs = np.nonzero(mask)

    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max()),
        int(ys.max()),
    )


rmse = float(np.sqrt(np.mean((reference - rendered) ** 2)))

print("reference_bbox", pink_bbox(reference))
print("rendered_bbox", pink_bbox(rendered))
print("rmse", rmse)

points = {
    "background": (0, 0),
    "deep_left": (65, 200),
    "center": (252, 243),
    "bottom_milk": (252, 430),
    "left_highlight": (153, 91),
    "right_highlight": (368, 382),
    "shadow": (278, 461),
}

for name, (x, y) in points.items():
    print(
        name,
        "reference",
        reference[y, x].astype(int).tolist(),
        "rendered",
        rendered[y, x].astype(int).tolist(),
    )
```

无需把 NumPy/Pillow 加入项目正式依赖，可以临时运行：

```bash
uv run --with numpy --with pillow --frozen python compare_shader.py
```

### 14.2 bbox 的含义和限制

本案例 mask：

```python
((red - green) > 15) & ((red - blue) > 4)
```

会同时捕获主体和粉色阴影，因此 bbox 表示的是“整体粉色可见范围”，不等于纯椭圆轮廓。

如果需要独立评估几何主体，可以增加：

- Y 范围限制；
- 饱和度或亮度限制；
- 参考圆心附近的连通域筛选；
- 主体 SDF 的解析边界对比。

### 14.3 RMSE 的作用和限制

公式：

\[
RMSE=\sqrt{\frac{1}{N}\sum_{i=1}^{N}
\left(I_{reference,i}-I_{rendered,i}\right)^2}
\]

RMSE 适合回答：

- 新候选整体上是否更接近；
- 某轮调参是否明显退化；
- 大面积背景、渐变和阴影是否对齐。

RMSE 不适合单独回答：

- 高光形状是否自然；
- 玻璃质感是否更好；
- 局部 1–2 px 的亮边是否符合审美；
- 颜色差异是否集中在视觉关键区域。

因此必须组合：

```text
bbox + 代表性像素 + RMSE + view_image 人工复核
```

## 15. 实际迭代记录

### 15.1 初始版本

```text
RMSE: 15.0372
```

主要问题：

- 阴影没有正确覆盖右侧外晕；
- 底部阴影一直延伸到画布边缘；
- 主体中心偏深；
- 左上高光过长、过软；
- 右下核心不够白；
- 主体像平面渐变。

### 15.2 阴影校准

```text
RMSE: 14.4407
参考 bbox: (38, 32, 488, 501)
渲染 bbox: (37, 31, 489, 502)
```

只修改两组阴影 Gaussian，不改主体颜色和高光。结果是 bbox 四边都缩小到 1 px 误差。

### 15.3 候选 1：调底色和高光

```text
RMSE: 11.4405
```

修改：

- 提亮 `deepPink`；
- 减弱暗色团；
- 缩短左上高光；
- 把左上核心向外移动；
- 把右下核心移动到代表性白点；
- 提高右下纯白混合强度。

指标明显改善，但人工复核仍认为主体偏平，左上代表点过暗。

### 15.4 候选 2：最终版本

```text
RMSE: 10.8290
参考 bbox: (38, 32, 488, 501)
渲染 bbox: (37, 31, 489, 502)
```

新增且仅新增：

- 一个 `innerHaze`；
- 一个 `innerSheen`。

最终关键像素：

```text
center
reference [253, 135, 170]
rendered  [254, 136, 172]

right_highlight
reference [254, 254, 254]
rendered  [255, 255, 255]

shadow
reference [254, 216, 231]
rendered  [252, 216, 229]
```

最终保留候选 2，不进行第三轮，避免无界调参。

## 16. 有限迭代策略

### 16.1 每轮只处理一个问题域

推荐：

```text
轮次 1：只调几何
轮次 2：只调阴影
轮次 3：只调主体颜色
轮次 4：只调高光
轮次 5：只调玻璃层次
```

不推荐在同一轮同时移动圆心、改变渐变、增加 haze 和重写高光。这样即使 RMSE 下降，也无法知道是哪项修改有效。

### 16.2 为每轮保存证据

至少记录：

- Shader hash；
- PNG hash；
- bbox；
- RMSE；
- 代表性像素；
- WebGL console；
- 一句话视觉结论。

### 16.3 明确停止条件

本案例采用：

- WebGL1 编译、链接、绘制成功；
- 控制台没有 Shader 错误；
- bbox 误差不超过约 2 px；
- 新候选 RMSE 不得明显退化；
- 主要视觉层全部存在；
- 每个问题域最多 1–2 个候选；
- 新候选更差时恢复上一最佳版本；
- 不为微小指标改善增加抽象、循环或运行时能力。

## 17. 常见失败模式

### 17.1 只使用一个线性渐变

症状：颜色大致正确，但像一张平面圆形贴纸。

原因：参考图的局部色彩分布不是严格平面函数。

修复：增加少量局部 Gaussian 色团和弱 `innerHaze`。

### 17.2 用一个大 Gaussian 处理全部阴影

症状：阴影延伸到画布底边，右侧和底部无法独立控制。

修复：将右侧外晕和底部柔影拆成两个各向异性 Gaussian。

### 17.3 用旋转椭圆直接做高光

症状：高光是一条直线胶囊，不沿圆周弯曲。

修复：使用方向 Gaussian 与径向 Gaussian 相乘。

### 17.4 通过降低强度修复“高光太长”

症状：高光仍然过长，只是变淡。

修复：缩小切向 sigma；强度只负责亮度，不负责长度。

### 17.5 直接平方大像素坐标

症状：部分最低规格 WebGL1 设备出现高光截断或异常。

修复：先除 sigma，再做点积或平方。

### 17.6 直接平方极小 radial 差值

症状：高光峰顶出现数像素平台，细亮边不稳定。

修复：先计算 `(radial-target)/sigma`，再平方。

### 17.7 只看 RMSE

症状：指标下降，但关键高光形状或玻璃质感反而变差。

修复：RMSE 必须与代表性像素和人工视觉复核结合。

### 17.8 只看页面是否打开

症状：HTML 可以加载，但 Shader 可能编译失败或 canvas 为空。

修复：显式检查 compile、link、`ready` 状态、console 和 canvas 截图。

### 17.9 让临时验证工具进入正式产物

症状：最终效果依赖一次性 HTML、额外服务器或调试脚本。

修复：验证结束后清理 preview、session 和 server，只保留必要产物。

## 18. 推广到其他参考图

### 18.1 适合本方法的图片

- 单主体；
- 背景简单；
- 轮廓能用圆、椭圆、圆角矩形或少量 SDF 表达；
- 颜色和光照连续；
- 没有大量文字、照片细节或复杂纹理；
- 高光和阴影可以用少量解析函数拟合。

### 18.2 何时需要升级方法

| 图片特征 | 建议升级 |
|---|---|
| 不规则轮廓 | 多 SDF 组合、Bezier SDF 或矢量路径 |
| 复杂重复纹理 | 程序噪声、FBM、Voronoi、周期函数 |
| 多物体遮挡 | 分层 SDF、深度顺序或离线生成 DSL |
| 强透视 | 局部坐标变换、透视投影或 ray marching |
| 真实折射 | 法线场、环境近似或允许贴图采样 |
| 极细曲线高光 | 曲线距离场或有限 supersampling |
| 大量照片级细节 | 放宽无贴图约束，或改为混合方法 |

### 18.3 通用分层模板

```glsl
void main() {
  // 1. 坐标系统
  vec2 p = ...;

  // 2. 主体 SDF/遮罩
  float mask = ...;

  // 3. 背景与主体外效果
  vec3 color = background(...);
  color = addShadowAndGlow(color, p, mask);

  // 4. 主体基础颜色
  vec3 body = baseGradient(p);

  // 5. 局部色团和介质层次
  body = addColorLobes(body, p);
  body = addHaze(body, p);

  // 6. 边缘和高光
  body = addRim(body, p);
  body = addHighlights(body, p);

  // 7. 合成
  color = mix(color, body, mask);
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
```

实际实现时不一定要真的抽成函数。对于单一效果，保持单文件和少量 helper 往往更清晰，也能减少 WebGL1 编译器负担。

## 19. 可执行检查清单

### 输入分析

- [ ] 已记录画布尺寸和背景色；
- [ ] 已测量主体 bbox、中心和尺寸；
- [ ] 已确定主渐变方向；
- [ ] 已拆分局部色团；
- [ ] 已测量高光中心、方向、长度和宽度；
- [ ] 已拆分阴影方向；
- [ ] 已选择代表性像素。

### Shader 实现

- [ ] 运行时契约正确；
- [ ] 坐标原点方向正确；
- [ ] 主体遮罩有像素尺度抗锯齿；
- [ ] Gaussian 先归一化再平方；
- [ ] 主体外效果不会污染主体内部；
- [ ] rim、描边、sheen 分层独立；
- [ ] 高光同时有方向约束和径向约束；
- [ ] 没有引入参考图不存在的噪声和细节；
- [ ] 最终 RGB 已 clamp；
- [ ] alpha 符合运行时要求。

### 真实渲染验证

- [ ] Fragment Shader 编译成功；
- [ ] Program 链接成功；
- [ ] canvas 绘制成功；
- [ ] 页面状态为 `ready`；
- [ ] console 无 Shader 错误；
- [ ] 输出 PNG 尺寸正确；
- [ ] bbox 已比较；
- [ ] 代表性像素已比较；
- [ ] RMSE 已计算；
- [ ] 已人工查看参考图和渲染图。

### 收尾

- [ ] 已保留最终 Shader 和渲染图；
- [ ] 已记录最终指标和残差；
- [ ] 已删除临时 preview；
- [ ] 已关闭浏览器 session；
- [ ] 已停止临时服务器；
- [ ] 已运行仓库文档检查；
- [ ] 没有把临时分析依赖加入正式运行环境。

## 20. 最终经验总结

1. 先确认 Shader 运行契约，再写视觉算法。
2. 把参考图拆成可独立控制的视觉层，不要只写一句自然语言描述。
3. 对固定图片，直接解析拟合通常比物理光照更容易精确复刻。
4. SDF 负责几何，Gaussian 负责局部分布，`smoothstep` 负责边带和抗锯齿。
5. 单一线性渐变很难产生材质感，需要少量局部色团和弱介质层。
6. 玻璃感主要来自宽 rim、窄描边、内部雾化、细亮边和双层高光。
7. 弧形高光可以用“方向 Gaussian × 径向 Gaussian”低成本实现。
8. WebGL1 `mediump` 下必须先归一化再平方，既防溢出也防下溢。
9. 先对齐几何和阴影，再优化颜色、高光和内部层次。
10. bbox、代表性像素、RMSE 和人工视觉复核缺一不可。
11. 每轮只改一个问题域，并限制候选数量，避免无界调参。
12. 临时验证工具只用于证明效果，最终运行产物应保持最小。
