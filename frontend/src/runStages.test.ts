import { describe, expect, it } from "vitest";

import type { MinRunProgressEvent } from "./api/shader";
import {
  buildRunViewModel,
  DIRECT_NODES,
  formatMetric,
  isTerminalRunStatus,
  mergeProgressEvents,
  nodeLabel,
  stopReasonLabel,
} from "./runStages";

describe("Direct progress stages", () => {
  it("contains the coordinator, attempt lifecycle, and all 20 graph nodes", () => {
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
      "decide_uniform_optimization",
      "propose_uniform_candidate",
      "apply_uniform_candidate",
      "record_uniform_outcome",
      "decide_refinement",
      "author_refinement",
      "apply_refinement",
      "release_resources",
      "finalize_attempt",
    ]);
    expect(nodeLabel("render_program")).toBe("执行渲染");
  });

  it("projects uniform tuning budget and safe summary without guessing missing values", () => {
    const view = buildRunViewModel({
      events: [],
      snapshot: {
        uniform_optimization: {
          schema_version: "uniform_optimization_summary_v2",
          evaluated_count: 3,
          accepted_count: 1,
          draw_count: 3,
          draw_budget: 4,
          initial_mae: 0.11,
          final_mae: 0.09,
          mae_delta: 0.02,
          initial_loss: 0.31,
          final_loss: 0.298,
          loss_delta: 0.012,
          stop_reason: "local_optimum",
        },
      },
      status: "succeeded",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });

    expect(view.budgets.find((budget) => budget.id === "uniform")).toEqual({
      id: "uniform",
      label: "参数搜索 draw",
      used: 3,
      budget: 4,
    });
    expect(view.uniformOptimization).toEqual({
      evaluatedCount: 3,
      acceptedCount: 1,
      maeDelta: 0.02,
      lossDelta: 0.012,
      stopReason: "local_optimum",
      stopReasonLabel: "参数搜索达到局部最优",
      candidateOutcome: null,
    });
  });

  it("keeps negative uniform deltas visible and drops non-finite summary values", () => {
    const view = buildRunViewModel({
      events: [],
      snapshot: {
        uniform_optimization: {
          mae_delta: -0.0025,
          loss_delta: Number.NaN,
        },
      },
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });

    expect(view.uniformOptimization?.maeDelta).toBe(-0.0025);
    expect(view.uniformOptimization?.lossDelta).toBeNull();
    expect(formatMetric(view.uniformOptimization?.maeDelta)).toBe("-0.0025");
  });

  it("labels exhausted candidate failures without exposing private diagnostics", () => {
    expect(stopReasonLabel("candidate_failures_exhausted")).toBe(
      "参数候选失败次数耗尽",
    );
    expect(stopReasonLabel("global_compile_budget_exhausted")).toBe(
      "全局编译预算用尽",
    );
  });

  it("uses the top-level terminal reason and safe uniform candidate outcome", () => {
    const view = buildRunViewModel({
      events: [],
      snapshot: {
        reason_code: "uniform_candidate_accepted",
        uniform_optimization: {
          draw_count: 2,
          draw_budget: 4,
          evaluated_count: 2,
          accepted_count: 1,
          candidate_outcome: "accepted",
        },
      },
      status: "succeeded",
      stopReason: "target_reached",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });

    expect(view.stopReasonLabel).toBe("达到质量目标");
    expect(view.reasonCode).toBe("uniform_candidate_accepted");
    expect(view.uniformOptimization?.candidateOutcome).toBe("accepted");
  });

  it("keeps the latest candidate outcome when the following decision overwrites snapshot", () => {
    const view = buildRunViewModel({
      events: [
        {
          seq: 12,
          node: "record_uniform_outcome",
          status: "completed",
          uniform_optimization: {
            draw_count: 2,
            draw_budget: 4,
            evaluated_count: 2,
            accepted_count: 1,
            candidate_outcome: "accepted",
          },
        },
        {
          seq: 13,
          node: "decide_uniform_optimization",
          status: "completed",
          uniform_optimization: {
            draw_count: 2,
            draw_budget: 4,
            evaluated_count: 2,
            accepted_count: 1,
          },
        },
      ],
      snapshot: {
        uniform_optimization: {
          draw_count: 2,
          draw_budget: 4,
          evaluated_count: 2,
          accepted_count: 1,
        },
      },
      status: "running",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });

    expect(view.uniformOptimization?.candidateOutcome).toBe("accepted");
  });

  it("requires both MAE and loss before reporting the target reached", () => {
    const view = buildRunViewModel({
      events: [],
      snapshot: {
        best: {
          mae: 0.05,
          loss: 0.03,
          target_mae: 0.04,
          target_loss: 0.06,
        },
      },
      status: "succeeded",
      nowSeconds: 10,
      mountedAtSeconds: 0,
    });

    expect(view.quality.targetReached).toBe(false);
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
