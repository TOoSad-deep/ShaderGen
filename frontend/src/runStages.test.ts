import { describe, expect, it } from "vitest";

import {
  buildRunViewModel,
  DIRECT_NODES,
  isTerminalRunStatus,
  mergeProgressEvents,
  nodeLabel,
} from "./runStages";

describe("Direct progress stages", () => {
  it("contains only the current coordinator and attempt stages", () => {
    expect(DIRECT_NODES.map((node) => node.id)).toEqual([
      "engine_rollout",
      "direct_glsl",
    ]);
    expect(nodeLabel("direct_glsl")).toBe("Layered 生成与渲染");
  });

  it("deduplicates progress events and recognizes terminal states", () => {
    const events = mergeProgressEvents(
      [{ seq: 1, node: "engine_rollout", status: "running" }],
      [
        { seq: 1, node: "engine_rollout", status: "running" },
        { seq: 2, node: "direct_glsl", status: "completed" },
      ],
    );
    expect(events.map((item) => item.seq)).toEqual([1, 2]);
    expect(isTerminalRunStatus("succeeded")).toBe(true);
  });

  it("keeps running backend stages visibly in progress", () => {
    const view = buildRunViewModel({
      events: [
        {
          seq: 1,
          node: "engine_rollout",
          phase: "engine_start",
          status: "running",
        },
      ],
      snapshot: null,
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    expect(view.stages[0].state).toBe("running");
    expect(view.completedStageCount).toBe(0);
  });

  it("treats a failed attempt followed by retry success as a successful stage", () => {
    const view = buildRunViewModel({
      events: [
        { seq: 1, node: "direct_glsl", phase: "direct_start", status: "running" },
        { seq: 2, node: "direct_glsl", phase: "direct_failed", status: "failed" },
        { seq: 3, node: "direct_glsl", phase: "direct_start", status: "running" },
        {
          seq: 4,
          node: "direct_glsl",
          phase: "direct_completed",
          status: "completed",
        },
      ],
      snapshot: null,
      status: "succeeded",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    expect(view.stages[1].state).toBe("completed");
    expect(view.stages[1].visits).toBe(2);
    expect(view.failure).toBeNull();
  });
});
