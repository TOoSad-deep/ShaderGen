import { describe, expect, it } from "vitest";

import {
  attemptStatusLabel,
  buildEngineRunView,
  engineLabel,
  representationLabel,
} from "./engineRun";

describe("engine run observability", () => {
  it("keeps old responses compatible without guessing an engine", () => {
    expect(buildEngineRunView(undefined, undefined, undefined)).toBeNull();
  });

  it("labels the two execution representations without treating LayerPlan as executable", () => {
    const direct = buildEngineRunView(
      "direct_glsl_layerplan_v1",
      "shader_program_spec_v1",
      null,
    );
    expect(direct?.engineLabel).toBe("Direct Program");
    expect(direct?.representationLabel).toBe("ShaderProgramSpec");
    expect(direct?.executionExplanation).toContain("LayerPlan 只提供");
    expect(direct?.executionExplanation).toContain("不是 GLSL 的执行表示");

    expect(engineLabel("shader_graph_v1")).toBe("ShaderGraph DSL");
    expect(representationLabel("shader_document_v1")).toBe("ShaderDocument");
  });

  it("marks the selected attempt and exposes an explicit fallback", () => {
    const view = buildEngineRunView(
      "shader_graph_v1",
      "shader_document_v1",
      {
        policy_id: "canary-v1",
        configured_stage: "canary",
        effective_stage: "canary",
        bucket: 42,
        selected_attempt_id: "attempt-old",
        fallback_from: "direct_glsl_layerplan_v1",
        fallback_reason: "direct_compile_failed",
        attempts: [
          {
            attempt_id: "attempt-direct",
            engine: "direct_glsl_layerplan_v1",
            representation: "shader_program_spec_v1",
            status: "failed",
            failure_code: "direct_compile_failed",
          },
          {
            attempt_id: "attempt-old",
            engine: "shader_graph_v1",
            representation: "shader_document_v1",
            status: "succeeded",
          },
        ],
      },
    );

    expect(view?.fallbackFromLabel).toBe("Direct Program");
    expect(view?.fallbackReason).toBe("direct_compile_failed");
    expect(view?.attempts.map((attempt) => attempt.selected)).toEqual([false, true]);
    expect(view?.attempts[0].failureCode).toBe("direct_compile_failed");
  });

  it("accepts the parent-envelope stage and attempt_refs aliases", () => {
    const view = buildEngineRunView("shader_graph_v1", "shader_document_v1", {
      stage: "production_shadow",
      selected_attempt_id: "attempt-product",
      attempt_refs: [
        {
          attempt_id: "attempt-product",
          engine: "shader_graph_v1",
          representation: "shader_document_v1",
          status: "succeeded",
        },
      ],
      shadow_submission: {
        status: "accepted",
        reason: "shadow_queued",
        attempt_id: "attempt-shadow",
      },
    });

    expect(view?.effectiveStage).toBe("production_shadow");
    expect(view?.attempts).toHaveLength(1);
    expect(view?.attempts[0].selected).toBe(true);
    expect(view?.shadowSubmissionStatus).toBe("accepted");
    expect(view?.shadowSubmissionReason).toBe("shadow_queued");
  });

  it("preserves unknown attempt status for forward compatibility", () => {
    expect(attemptStatusLabel("retrying")).toBe("retrying");
    expect(attemptStatusLabel(null)).toBe("状态未知");
  });
});
