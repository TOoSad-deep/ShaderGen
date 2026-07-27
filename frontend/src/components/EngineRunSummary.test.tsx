import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EngineRunSummary } from "./EngineRunSummary";

describe("EngineRunSummary", () => {
  it("renders nothing for a legacy response without discriminator fields", () => {
    expect(renderToStaticMarkup(<EngineRunSummary />)).toBe("");
  });

  it("shows execution source, attempts and fallback without exposing private artifacts", () => {
    const html = renderToStaticMarkup(
      <EngineRunSummary
        engine="shader_graph_v1"
        representation="shader_document_v1"
        engineRun={{
          stage: "canary",
          selected_attempt_id: "old-attempt",
          fallback_from: "direct_glsl_layerplan_v1",
          fallback_reason: "direct_compile_failed",
          attempt_refs: [
            {
              attempt_id: "direct-attempt",
              engine: "direct_glsl_layerplan_v1",
              representation: "shader_program_spec_v1",
              status: "failed",
              failure_code: "direct_compile_failed",
            },
            {
              attempt_id: "old-attempt",
              engine: "shader_graph_v1",
              representation: "shader_document_v1",
              status: "succeeded",
            },
          ],
        }}
      />,
    );

    expect(html).toContain("ShaderGraph DSL");
    expect(html).toContain("LayerPlan 不参与该结果的执行");
    expect(html).toContain("本次发生显式 fallback");
    expect(html).toContain("Direct Program");
    expect(html).toContain("最终采用");
    expect(html).toContain("direct_compile_failed");
    expect(html).not.toContain("fragment_source");
    expect(html).not.toContain("prompt");
  });

  it("distinguishes shadow queue admission from execution success", () => {
    const html = renderToStaticMarkup(
      <EngineRunSummary
        engine="shader_graph_v1"
        representation="shader_document_v1"
        engineRun={{
          stage: "production_shadow",
          shadow_submission: {
            status: "accepted",
            reason: "shadow_queued",
            attempt_id: "shadow-attempt",
          },
        }}
      />,
    );

    expect(html).toContain("Direct shadow 提交：已接收");
    expect(html).toContain("不代表其执行成功");
    expect(html).toContain("不影响本次产品结果");
  });
});
