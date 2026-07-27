import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  resolveRecommendedBaseStepId,
  type StepLike,
} from "../src/utils/nodeLabBaseStep.ts";

function step(
  step_id: string,
  node_id: string,
  execution_status: StepLike["execution_status"] = "completed",
): StepLike {
  return { step_id, node_id, execution_status };
}

describe("resolveRecommendedBaseStepId", () => {
  it("无父节点时返回 Root", () => {
    assert.equal(resolveRecommendedBaseStepId(null, [step("s1", "initialize_run")]), "");
  });

  it("从同 node_id 步骤中选最新一个", () => {
    const steps = [
      step("s1", "initialize_run"),
      step("s2", "perceive_target"),
      step("s3", "perceive_target"),
    ];
    assert.equal(resolveRecommendedBaseStepId("perceive_target", steps), "s3");
  });

  it("忽略失败步骤", () => {
    const steps = [
      step("s1", "perceive_target", "failed"),
      step("s2", "perceive_target", "completed"),
    ];
    assert.equal(resolveRecommendedBaseStepId("perceive_target", steps), "s2");
  });

  it("全部失败时返回 Root", () => {
    const steps = [
      step("s1", "perceive_target", "failed"),
      step("s2", "perceive_target", "failed"),
    ];
    assert.equal(resolveRecommendedBaseStepId("perceive_target", steps), "");
  });

  it("无匹配 node_id 时返回 Root", () => {
    const steps = [step("s1", "initialize_run")];
    assert.equal(resolveRecommendedBaseStepId("perceive_target", steps), "");
  });
});
