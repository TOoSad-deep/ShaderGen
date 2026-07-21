# V2 release-held-out 独立保管人交接手册

本文只供**未参与 ShaderGen 开发、且持有封存素材的独立发布保管人**使用。开发人员不得接触 release 图片、逐例标签、`case_id`、文件名、来源 URL 或逐例评估结果。仓库内的 `release-held-out` 必须继续保持 `not_populated`；本流程不会选图、下载图片、生成标签或把发布集写回仓库。

## 交接边界

- 保管人在仓库之外的封存机器/账户中建立 release package。package 必须包含三 split Manifest、taxonomy、原图和来源/许可记录，但不得被复制到开发工作区。
- `development`、`validation`、`release-held-out` 的图片 SHA-256、`visual_family`、`hash_group` 必须三重隔离；开发侧见过的 16/40/41 张素材都不能因为数量足够而自动转成 release。
- release 的六个关键类均需至少 10 个正例：`multi_instance`、`ring`、`hollow`、`required_highlight`、`required_rim`、`required_outline`。同一张图片可以有多个真实标签，因此 40 张**可能**够，也可能不够；是否满足只能由完整标签聚合后的六个 `numerator/10` 决定。
- 生产门槛固定为 10。测试代码可使用 synthetic package，但 CLI 不提供降低门槛的选项。
- 交给开发侧的发布证据只允许是签名 readiness attestation，以及后续同样 aggregate-only 的发布评估；验签所需 Ed25519 公钥和独立渠道预登记的公钥 SHA-256 可一并交付。不得交付 package、Manifest、freeze Manifest、签名私钥、逐例结果或日志。

## 外部目录结构

下面仅是目录规范，不是样本，也不构成 ready 证据：

```text
/sealed/v2-release-package/
├── dataset_manifest.v1.json
├── expected_primitives_taxonomy.v1.json
├── images/
│   ├── development/...
│   ├── validation/...
│   └── release-held-out/...
└── sources/
    ├── development.v1.md
    ├── validation.v1.md
    └── release-held-out-<suite>.v1.md

/sealed/v2-release-control/
├── operator-signing-private.pem
├── operator-signing-public.pem
├── v2.3-rc1.freeze.json
└── v2.3-rc1.readiness-attestation.json
```

Ed25519 私钥只留在保管人环境；公钥及其由发布负责人预先登记的 raw-key SHA-256 是开发侧的信任根。不能把 attestation 自带的公钥身份当作唯一信任来源。`freeze.json` 也不交给开发侧，因为它与完整 package 的身份绑定；交付开发侧的是最终脱敏 attestation、公钥，以及通过独立可信渠道预先确认的公钥 SHA-256。

## Manifest 与来源/许可模板

Manifest 必须使用现有 `png_to_shader_dataset_manifest_v1`，保留固定的三 split 顺序。`release-held-out` 应为：

```json
{
  "name": "release-held-out",
  "status": "available",
  "access_policy": "sealed_release_test",
  "purpose": "V2.3 独立发布门禁",
  "samples": [
    {
      "case_id": "由保管人分配的内部 id",
      "dataset_role": "evaluation",
      "source_suite_id": "来源批次 id",
      "image": "package 根相对路径",
      "sha256": "原图内容 SHA-256",
      "resolution": [1, 1],
      "visual_family": "完整视觉族 id",
      "hash_group": "近重复/派生内容组 id",
      "topology": "solid|hollow|ring|open",
      "instance_count": 1,
      "hole_count": 0,
      "required_layers": ["highlight", "rim", "outline"],
      "expected_primitives": {
        "taxonomy_version": "png_to_shader_expected_primitives_v1",
        "items": ["taxonomy 中已登记的 primitive id"]
      }
    }
  ]
}
```

上例只是字段规范，`resolution`、结构和 layer 值必须来自真实标注，不能照抄。`ring/hollow` 必须有 `hole_count >= 1`，`solid` 必须为 0。每个 `source_suite_id` 必须精确对应一个 `source_records` 项：

```json
{
  "source_suite_id": "来源批次 id",
  "provenance_path": "sources/release-held-out-<suite>.v1.md",
  "provenance_sha256": "来源文档 SHA-256",
  "source_url": "https://原始素材页面",
  "license_id": "明确的 SPDX/许可名称",
  "license_url": "https://许可正文"
}
```

来源文档至少原样包含 `source_url`、`license_id`、`license_url`，并记录下载时间、原作者/提供方、选择人和许可核验人。`unknown`、`TBD`、本地路径以及缺失许可均会 fail closed。若一份来源文档覆盖多个 suite，Manifest 中每条记录仍需绑定同一文档的真实 SHA-256。

## 保管人操作步骤

以下命令在**保管人自己的隔离环境**执行；路径仅作示例。首先由发布负责人提供已经冻结的 64 位小写 `CODE_CONFIG_SHA256`。这个 hash 应绑定 V2.3 RC 的代码、Prompt、模板、配置和阈值，保管人不得自行替换。

1. 在外部 package 中完成图片、Manifest、taxonomy 和来源/许可文档；不要运行浏览或打印逐例内容的调试命令。
2. 创建仅保管人可读的 Ed25519 私钥并导出公钥；发布负责人通过独立可信渠道登记公钥 raw bytes 的 SHA-256，记为 `EXPECTED_PUBLIC_KEY_SHA256`：

   ```bash
   umask 077
   openssl genpkey -algorithm Ed25519 \
     -out /sealed/v2-release-control/operator-signing-private.pem
   openssl pkey \
     -in /sealed/v2-release-control/operator-signing-private.pem \
     -pubout \
     -out /sealed/v2-release-control/operator-signing-public.pem
   ```

   `EXPECTED_PUBLIC_KEY_SHA256` 是 Ed25519 32-byte raw public key 的 SHA-256，不是 PEM 文件 SHA。可在登记环境用 `cryptography` 读取公钥后计算；登记人应通过与 attestation/package 不同的可信渠道把该值交给开发侧。

3. 完整校验 package 并 exclusive-create 签名冻结记录：

   ```bash
   uv run python scripts/run_v2_release_operator_handoff.py freeze \
     --package-root /sealed/v2-release-package \
     --manifest dataset_manifest.v1.json \
     --freeze-manifest /sealed/v2-release-control/v2.3-rc1.freeze.json \
     --freeze-label v2.3-rc1 \
     --expected-code-config-sha256 "$CODE_CONFIG_SHA256" \
     --signing-private-key /sealed/v2-release-control/operator-signing-private.pem \
     --signing-key-id release-operator-key-v1
   ```

4. 使用同一 package、freeze 和预期 code/config hash 执行 readiness：

   ```bash
   uv run python scripts/run_v2_release_operator_handoff.py evaluate \
     --package-root /sealed/v2-release-package \
     --manifest dataset_manifest.v1.json \
     --freeze-manifest /sealed/v2-release-control/v2.3-rc1.freeze.json \
     --expected-code-config-sha256 "$CODE_CONFIG_SHA256" \
     --signing-private-key /sealed/v2-release-control/operator-signing-private.pem \
     --trusted-public-key /sealed/v2-release-control/operator-signing-public.pem \
     --expected-public-key-sha256 "$EXPECTED_PUBLIC_KEY_SHA256" \
     --signing-key-id release-operator-key-v1 \
     --output /sealed/v2-release-control/v2.3-rc1.readiness-attestation.json
   ```

5. 命令退出码为 0 且 attestation 的 `ready=true` 时，六类 `numerator/denominator` 必须全部至少 `10/10`。退出码 2 表示 blocked；分母仍留在 attestation，错误只按安全类别计数，不得用修改门槛、删除失败样本或覆盖输出文件来规避。
6. 将**唯一**的 `readiness-attestation.json` 和 Ed25519 公钥交付开发侧；公钥 SHA-256 必须从此前独立可信渠道取得，不能从本次 attestation 临时相信。开发侧只做公开验签，不读取 release package：

   ```bash
   uv run python scripts/run_v2_release_operator_handoff.py verify \
     --attestation v2.3-rc1.readiness-attestation.json \
     --trusted-public-key release-operator-signing-public.pem \
     --expected-public-key-sha256 "$EXPECTED_PUBLIC_KEY_SHA256" \
     --expected-code-config-sha256 "$CODE_CONFIG_SHA256" \
     --expected-freeze-label v2.3-rc1 \
     --expected-stage v2_3_release_candidate
   ```

   `verify` 四重绑定受信公钥、consumer 预期 code/config SHA-256、freeze label 和固定 stage；即使旧 RC attestation 由同一保管人合法签名，也不能作为新 RC 证据重放。

   工具使用 exclusive-create；重跑必须使用新的冻结标签和新的输出文件，不能覆盖已有证据。

## 工具验证内容与脱敏保证

`evaluate` 必须以 `gate_stage="v2_3_release_candidate"` 调用 `load_v2_dataset_manifest()`，再调用 `evaluate_v2_dataset_stage_gate()`。因此它会复验原图/来源文件 SHA-256、图片尺寸、taxonomy、冻结版本、六类分母，以及三 split 的 SHA/视觉族/hash group 污染。

attestation 和默认 stdout 仅包含：`ready`、`package_verified`、六类聚合 `numerator/denominator`、release 样本总数、Manifest/taxonomy/package SHA-256、code/config SHA-256、freeze label、执行时间、工具版本、Ed25519 key id/公钥 SHA-256、签名和 blocker 类别计数。`package_verified=false` 时仍保留 Manifest 声明的六类分母，但不得把它们解释成已经通过原图/许可复验。输出不得包含 release `case_id`、文件名、路径、来源 URL、逐例标签或逐例结果。内部异常统一脱敏，失败不会把敏感异常字符串写到 stdout 或 attestation。

## 后续 aggregate-only 发布评估

readiness 只证明数据包完整且类分母满足门槛，不代表模型质量通过。V2.3 RC 后续真实发布评估仍必须在同一保管环境、同一 freeze 绑定和显式模型预算下运行；给开发侧的结果只能是预先约定的整体/分类聚合指标与签名，不得含逐例 output。若代码、配置、Prompt、阈值或 release package 任一内容变化，旧 freeze 和 attestation 立即失效，必须使用新 label 重新冻结。
