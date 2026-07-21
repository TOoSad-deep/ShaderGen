# V1 确定性回归图来源记录（v1）

本记录覆盖 V2 development split 中 `source_suite_id=png_to_shader_v1_m0` 的 10 张回归 PNG。

- 来源：ShaderGen 仓库内确定性 fixture 生成器；不是外部下载素材。
- 用途：只读 regression，不计入 validation 或 release-held-out 分母。
- 许可：随 ShaderGen 仓库自身许可分发；不引入第三方图片许可。
- 内容身份：每张图片仍以 `dataset_manifest.v1.json` 内 SHA-256 为准。

这些图片已暴露给开发与测试代码，不得进入 release-held-out。
