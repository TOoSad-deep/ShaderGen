import { describe, expect, it } from "vitest";

import { buildEngineRunView } from "./engineRun";

describe("buildEngineRunView", () => {
  it("marks the selected Direct attempt and preserves safe failures", () => {
    const view = buildEngineRunView({
      selected_attempt_id: "attempt-2",
      attempt_refs: [
        {
          attempt_id: "attempt-1",
          engine: "direct_glsl_layerplan_v1",
          representation: "shader_program_spec_v1",
          status: "failed",
          failure_code: "direct_attempt_failed",
        },
        {
          attempt_id: "attempt-2",
          engine: "direct_glsl_layerplan_v1",
          representation: "shader_program_spec_v1",
          status: "succeeded",
        },
      ],
    });
    expect(view?.attempts).toHaveLength(2);
    expect(view?.attempts[0].failureCode).toBe("direct_attempt_failed");
    expect(view?.attempts[1].selected).toBe(true);
  });
});
