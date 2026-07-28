import type {
  ShaderEngineAttemptSummary,
  ShaderEngineRunSummary,
} from "./api/shader";

const STATUS_LABELS: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
};

export interface EngineAttemptView {
  attemptId: string | null;
  statusLabel: string;
  failureCode: string | null;
  selected: boolean;
}

export interface EngineRunView {
  engineLabel: string;
  representationLabel: string;
  executionExplanation: string;
  selectedAttemptId: string | null;
  attempts: EngineAttemptView[];
}

function attemptView(
  attempt: ShaderEngineAttemptSummary,
  selectedAttemptId: string | null,
): EngineAttemptView {
  const attemptId = attempt.attempt_id?.trim() || null;
  const status = attempt.status?.trim() || "unknown";
  return {
    attemptId,
    statusLabel: STATUS_LABELS[status] ?? status,
    failureCode: attempt.failure_code?.trim() || null,
    selected: attemptId !== null && attemptId === selectedAttemptId,
  };
}

export function buildEngineRunView(
  engineRun: ShaderEngineRunSummary | null | undefined,
): EngineRunView | null {
  if (!engineRun) return null;
  const selectedAttemptId = engineRun.selected_attempt_id?.trim() || null;
  return {
    engineLabel: "Layered Direct GLSL",
    representationLabel: "ShaderProgramSpec",
    executionExplanation:
      "LayerPlan 与 LayeredShaderSpec 由模型生成，确定性编译为 ShaderProgramSpec 后通过 WebGL1 实际编译、链接和绘制。",
    selectedAttemptId,
    attempts: (engineRun.attempt_refs ?? []).map((item) =>
      attemptView(item, selectedAttemptId),
    ),
  };
}
