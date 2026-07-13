# PNG to Shader V1 Benchmark

本目录保存 F09 V1 的确定性参考图和验收规格。

## 文件

- `manifest.yaml`：样例元数据、PNG SHA-256、目标能力、关键 ROI 和初始容差；
- `images/*.png`：由 `scripts/build_png_to_shader_v1_benchmarks.py` 生成的 10 张参考图。

## 生成

```bash
uv run python scripts/build_png_to_shader_v1_benchmarks.py
```

生成脚本只使用 Python 标准库，固定尺寸、公式和压缩设置。修改公式或尺寸后必须重新生成全部图片、更新 manifest 中对应 SHA-256，并运行 M0 测试。

这些样例只用于验证 V1 的契约、基础视觉层和后续指标单调性，不代表复杂照片的通用复刻能力。
