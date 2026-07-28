import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EngineRunSummary } from "./EngineRunSummary";

describe("EngineRunSummary", () => {
  it("renders Direct attempts without legacy policy UI", () => {
    const html = renderToStaticMarkup(
      <EngineRunSummary
        engineRun={{
          selected_attempt_id: "attempt-1",
          attempt_refs: [
            {
              attempt_id: "attempt-1",
              engine: "direct_glsl_layerplan_v1",
              representation: "shader_program_spec_v1",
              status: "succeeded",
            },
          ],
        }}
      />,
    );
    expect(html).toContain("Layered Direct GLSL");
    expect(html).toContain("最终采用");
    expect(html).not.toContain("policy");
  });
});
