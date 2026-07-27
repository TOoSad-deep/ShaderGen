import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildExampleInputs,
  fillArtifactInputs,
  formatArtifactOption,
  getArtifactCandidates,
  getArtifactFieldSelection,
  isArtifactPlaceholder,
  materializeExampleInputs,
  updateArtifactInputField,
  type ArtifactEquivalenceContext,
  type ArtifactLike,
} from "../src/utils/nodeLabInputs.ts";

function artifact(
  artifact_id: string,
  kind: string,
  sha256: string,
  created_at?: string,
): ArtifactLike {
  return { artifact_id, kind, sha256, created_at };
}

function contextWithBaseStep(field: string, artifactId: string): ArtifactEquivalenceContext {
  return {
    baseStep: {
      output: { [field]: artifactId },
    },
  };
}

describe("isArtifactPlaceholder", () => {
  it("把空字符串、缺失和 replace-with-*-artifact-id 视为占位符", () => {
    assert.equal(isArtifactPlaceholder(""), true);
    assert.equal(isArtifactPlaceholder(undefined), true);
    assert.equal(isArtifactPlaceholder("replace-with-uploaded-artifact-id"), true);
    assert.equal(isArtifactPlaceholder("REPLACE-WITH-FOO-ARTIFACT-ID"), true);
  });

  it("不把手写 Artifact ID 或其他值视为占位符", () => {
    assert.equal(isArtifactPlaceholder("abc-123"), false);
    assert.equal(isArtifactPlaceholder("replace-with-uploaded-id"), false);
    assert.equal(isArtifactPlaceholder("replace-with-artifact-id"), false);
    assert.equal(isArtifactPlaceholder(0), false);
    assert.equal(isArtifactPlaceholder(null), false);
  });
});

describe("getArtifactCandidates", () => {
  it("按 kind 过滤", () => {
    const artifacts = [
      artifact("a1", "reference_png", "sha1", "2026-07-27T10:00:00Z"),
      artifact("a2", "reference_png", "sha2", "2026-07-27T12:00:00Z"),
      artifact("b1", "target_rgb_npy", "sha3", "2026-07-27T11:00:00Z"),
    ];
    const candidates = getArtifactCandidates("reference_png", artifacts);
    assert.deepEqual(
      candidates.map((a) => a.artifact_id),
      ["a1", "a2"],
    );
  });

  it("无匹配时返回空数组", () => {
    assert.deepEqual(getArtifactCandidates("missing", []), []);
  });
});

describe("fillArtifactInputs", () => {
  const mapping = {
    source_artifact_id: "reference_png",
    target_rgb_artifact_id: "target_rgb_npy",
  };

  it("用唯一匹配 Artifact 填充占位字段", () => {
    const inputs = { source_artifact_id: "replace-with-uploaded-artifact-id" };
    const artifacts = [artifact("ref-1", "reference_png", "sha1")];
    const result = fillArtifactInputs(inputs, mapping, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-1");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
    assert.deepEqual(result.ambiguousFields, []);
  });

  it("不覆盖用户手写的非占位值", () => {
    const inputs = { source_artifact_id: "user-keeps-this" };
    const artifacts = [artifact("ref-1", "reference_png", "sha1")];
    const result = fillArtifactInputs(inputs, mapping, artifacts);
    assert.equal(result.inputs.source_artifact_id, "user-keeps-this");
    assert.deepEqual(result.filledFields, []);
  });

  it("对缺失字段视为空并填充", () => {
    const inputs = {};
    const artifacts = [artifact("ref-1", "reference_png", "sha1")];
    const result = fillArtifactInputs(inputs, mapping, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-1");
  });

  it("两个不同 sha256 的真实候选时保留占位符并标记 ambiguous", () => {
    const inputs = { source_artifact_id: "" };
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-a", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-b", "2026-07-27T12:00:00Z"),
    ];
    const result = fillArtifactInputs(inputs, mapping, artifacts);
    assert.equal(result.inputs.source_artifact_id, "");
    assert.deepEqual(result.filledFields, []);
    assert.deepEqual(result.ambiguousFields, ["source_artifact_id"]);
  });

  it("内容等价副本（sha256 相同）可自动填充", () => {
    const inputs = { source_artifact_id: "" };
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-same", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-same", "2026-07-27T12:00:00Z"),
    ];
    const result = fillArtifactInputs(inputs, mapping, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-1");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
    assert.deepEqual(result.ambiguousFields, []);
  });

  it("等价副本优先选择父 Step State 中引用的 ID", () => {
    const inputs = { source_artifact_id: "" };
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-same", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-same", "2026-07-27T12:00:00Z"),
    ];
    const result = fillArtifactInputs(
      inputs,
      mapping,
      artifacts,
      contextWithBaseStep("source_artifact_id", "ref-2"),
    );
    assert.equal(result.inputs.source_artifact_id, "ref-2");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
    assert.deepEqual(result.ambiguousFields, []);
  });

  it("父 Step 首次写入的字段可从 state_diff.added 匹配", () => {
    const inputs = { source_artifact_id: "" };
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-same", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-same", "2026-07-27T12:00:00Z"),
    ];
    const result = fillArtifactInputs(inputs, mapping, artifacts, {
      baseStep: { state_diff: { added: { source_artifact_id: "ref-2" } } },
    });
    assert.equal(result.inputs.source_artifact_id, "ref-2");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
  });

  it("父 Step State 引用的 ID 不在等价组内时回退到最早创建者", () => {
    const inputs = { source_artifact_id: "" };
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-same", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-same", "2026-07-27T12:00:00Z"),
    ];
    const result = fillArtifactInputs(
      inputs,
      mapping,
      artifacts,
      contextWithBaseStep("source_artifact_id", "other-id"),
    );
    assert.equal(result.inputs.source_artifact_id, "ref-1");
  });

  it("无匹配 Artifact 时保留占位符", () => {
    const inputs = { source_artifact_id: "replace-with-uploaded-artifact-id" };
    const result = fillArtifactInputs(inputs, mapping, []);
    assert.equal(result.inputs.source_artifact_id, "replace-with-uploaded-artifact-id");
    assert.deepEqual(result.filledFields, []);
  });
});

describe("updateArtifactInputField", () => {
  it("在合法 JSON 中更新字段并格式化", () => {
    const text = '{"source_artifact_id":"old","quality_preset":"fast"}';
    const next = updateArtifactInputField(text, "source_artifact_id", "new-id");
    assert.equal(
      next,
      JSON.stringify({ source_artifact_id: "new-id", quality_preset: "fast" }, null, 2),
    );
  });

  it("非法 JSON 返回 null", () => {
    assert.equal(updateArtifactInputField("not json", "x", "y"), null);
  });

  it("非 object 根返回 null", () => {
    assert.equal(updateArtifactInputField("[1,2]", "x", "y"), null);
  });
});

describe("getArtifactFieldSelection", () => {
  const candidates = [artifact("ref-1", "reference_png", "sha1")];

  it("当前值匹配候选时返回该 ID", () => {
    assert.deepEqual(getArtifactFieldSelection("ref-1", candidates), {
      selectedArtifactId: "ref-1",
      isKnown: true,
    });
  });

  it("当前值不在候选中时返回空选择", () => {
    assert.deepEqual(getArtifactFieldSelection("user-value", candidates), {
      selectedArtifactId: "",
      isKnown: false,
    });
  });
});

describe("formatArtifactOption", () => {
  it("输出短 ID 和时间戳", () => {
    const option = formatArtifactOption(
      artifact("ref-uuid-1234", "reference_png", "sha1", "2026-07-27T12:34:56Z"),
    );
    assert.equal(option, "ref-uuid · 2026-07-27T12:34:56");
  });

  it("缺少时间时只输出短 ID", () => {
    const option = formatArtifactOption(artifact("abc-123", "reference_png", "sha1"));
    assert.equal(option, "abc-123");
  });
});

describe("buildExampleInputs / materializeExampleInputs", () => {
  const example = {
    inputs: {
      source_artifact_id: "replace-with-uploaded-artifact-id",
      quality_preset: "fast",
      instruction: "复刻参考图",
    },
    artifact_inputs: { source_artifact_id: "reference_png" },
  };

  it("新 Run 无 Artifact 时保留示例占位符和普通默认值", () => {
    const result = buildExampleInputs(example, []);
    assert.equal(result.inputs.source_artifact_id, "replace-with-uploaded-artifact-id");
    assert.equal(result.inputs.quality_preset, "fast");
    assert.deepEqual(result.filledFields, []);
  });

  it("恢复 Run 有唯一匹配 Artifact 时自动填充", () => {
    const artifacts = [artifact("ref-uuid", "reference_png", "sha1")];
    const result = buildExampleInputs(example, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-uuid");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
  });

  it("同 kind 多个不同 Artifact 时保留占位符", () => {
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-a", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-b", "2026-07-27T12:00:00Z"),
    ];
    const result = buildExampleInputs(example, artifacts);
    assert.equal(result.inputs.source_artifact_id, "replace-with-uploaded-artifact-id");
    assert.deepEqual(result.filledFields, []);
    assert.deepEqual(result.ambiguousFields, ["source_artifact_id"]);
  });

  it("同 kind 多个等价副本时自动填充最早创建者", () => {
    const artifacts = [
      artifact("ref-1", "reference_png", "sha-same", "2026-07-27T10:00:00Z"),
      artifact("ref-2", "reference_png", "sha-same", "2026-07-27T12:00:00Z"),
    ];
    const result = buildExampleInputs(example, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-1");
    assert.deepEqual(result.filledFields, ["source_artifact_id"]);
    assert.deepEqual(result.ambiguousFields, []);
  });

  it("对示例 inputs 中缺失的 artifact 字段也能物化", () => {
    const exampleMissing = {
      inputs: { quality_preset: "fast" },
      artifact_inputs: { source_artifact_id: "reference_png" },
    };
    const artifacts = [artifact("ref-uuid", "reference_png", "sha1")];
    const result = buildExampleInputs(exampleMissing, artifacts);
    assert.equal(result.inputs.source_artifact_id, "ref-uuid");
  });

  it("materializeExampleInputs 返回格式化 JSON", () => {
    const artifacts = [artifact("ref-uuid", "reference_png", "sha1")];
    const text = materializeExampleInputs(example, artifacts);
    assert.equal(
      text,
      JSON.stringify(
        {
          source_artifact_id: "ref-uuid",
          quality_preset: "fast",
          instruction: "复刻参考图",
        },
        null,
        2,
      ),
    );
  });

  it("target_rgb_artifact_id 唯一候选时自动填充", () => {
    const renderExample = {
      inputs: { target_rgb_artifact_id: "replace-with-target-rgb-artifact-id" },
      artifact_inputs: { target_rgb_artifact_id: "target_rgb_npy" },
    };
    const artifacts = [artifact("rgb-uuid", "target_rgb_npy", "sha1")];
    const result = buildExampleInputs(renderExample, artifacts);
    assert.equal(result.inputs.target_rgb_artifact_id, "rgb-uuid");
    assert.deepEqual(result.filledFields, ["target_rgb_artifact_id"]);
  });
});
