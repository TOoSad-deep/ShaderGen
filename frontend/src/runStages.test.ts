import { describe, expect, it } from "vitest";

import type { MinRunProgressEvent } from "./api/shader";
import {
  buildRunViewModel,
  DIRECT_NODES,
  isTerminalRunStatus,
  mergeProgressEvents,
  nodeLabel,
} from "./runStages";

describe("Direct progress stages", () => {
  it("contains the coordinator, attempt lifecycle, and all 16 graph nodes", () => {
    expect(DIRECT_NODES.map((node) => node.id)).toEqual([
      "engine_rollout",
      "direct_glsl",
      "prepare_reference",
      "author_layer_plan",
      "author_initial",
      "compile_candidate",
      "validate_candidate",
      "prepare_program",
      "render_program",
      "verify_receipt",
      "attest_candidate",
      "evaluate_candidate",
      "select_candidate",
      "decide_refinement",
      "author_refinement",
      "apply_refinement",
      "release_resources",
      "finalize_attempt",
    ]);
    expect(nodeLabel("render_program")).toBe("执行渲染");
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

  it("tracks repeated graph-node visits without inventing a linear next route", () => {
    const view = buildRunViewModel({
      events: [
        { seq: 1, node: "compile_candidate", status: "running" },
        { seq: 2, node: "compile_candidate", status: "completed" },
        { seq: 3, node: "compile_candidate", status: "running" },
      ],
      snapshot: null,
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    const compile = view.stages.find((stage) => stage.id === "compile_candidate");
    expect(compile?.state).toBe("running");
    expect(compile?.visits).toBe(2);
    expect(view.nextStageId).toBeNull();
  });

  it("marks unvisited conditional nodes as skipped after a terminal result", () => {
    const view = buildRunViewModel({
      events: [
        { seq: 1, node: "engine_rollout", status: "completed" },
        { seq: 2, node: "finalize_attempt", status: "completed" },
      ],
      snapshot: null,
      status: "succeeded",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    expect(
      view.stages.find((stage) => stage.id === "author_refinement")?.state,
    ).toBe("skipped");
    expect(
      view.stages.find((stage) => stage.id === "finalize_attempt")?.state,
    ).toBe("completed");
  });

  it("uses the latest attempt for node state while retaining total visits", () => {
    const view = buildRunViewModel({
      events: [
        {
          seq: 1,
          node: "author_refinement",
          status: "running",
          attempt_index: 0,
        },
        {
          seq: 2,
          node: "author_refinement",
          status: "failed",
          attempt_index: 0,
        },
        {
          seq: 3,
          node: "prepare_reference",
          status: "completed",
          attempt_index: 1,
        },
        {
          seq: 4,
          node: "direct_glsl",
          status: "completed",
          attempt_index: 1,
        },
      ],
      snapshot: null,
      status: "succeeded",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    const refinement = view.stages.find(
      (stage) => stage.id === "author_refinement",
    );
    expect(refinement?.state).toBe("skipped");
    expect(refinement?.visits).toBe(1);
  });

  it("clears a stale route when a looped node starts a new visit", () => {
    const view = buildRunViewModel({
      events: [
        { seq: 1, node: "compile_candidate", status: "running" },
        {
          seq: 2,
          node: "compile_candidate",
          status: "completed",
          next_action: "validate_candidate",
        },
        { seq: 3, node: "compile_candidate", status: "running" },
      ],
      snapshot: null,
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    const compile = view.stages.find((stage) => stage.id === "compile_candidate");
    expect(compile?.nextAction).toBeNull();
    expect(compile?.visits).toBe(2);
  });

  it("does not render an unknown event status as completed", () => {
    const invalid = {
      seq: 1,
      node: "prepare_reference",
      status: "mystery",
    } as unknown as MinRunProgressEvent;
    const view = buildRunViewModel({
      events: [invalid],
      snapshot: null,
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });
    expect(view.stages.find((stage) => stage.id === "prepare_reference")?.state).toBe(
      "pending",
    );
    expect(view.unknownEventCount).toBe(1);
  });
});
