import type {
  ShaderEngineAttemptSummary,
  ShaderEngineId,
  ShaderEngineRunSummary,
  ShaderRepresentation,
} from "./api/shader";

const ENGINE_LABELS: Record<ShaderEngineId, string> = {
  shader_graph_v1: "ShaderGraph DSL",
  direct_glsl_layerplan_v1: "Direct Program",
};

const REPRESENTATION_LABELS: Record<ShaderRepresentation, string> = {
  shader_document_v1: "ShaderDocument",
  shader_program_spec_v1: "ShaderProgramSpec",
};

const ATTEMPT_STATUS_LABELS: Record<string, string> = {
  accepted: "已接收",
  pending: "等待执行",
  queued: "已排队",
  running: "执行中",
  succeeded: "成功",
  success: "成功",
  ok: "成功",
  failed: "失败",
  timeout: "超时",
  cancelled: "已取消",
  skipped: "已跳过",
};

export interface EngineAttemptView {
  attemptId: string | null;
  engine: ShaderEngineId | null;
  engineLabel: string;
  representation: ShaderRepresentation | null;
  representationLabel: string;
  status: string | null;
  statusLabel: string;
  failureCode: string | null;
  selected: boolean;
}

export interface EngineRunView {
  engine: ShaderEngineId | null;
  engineLabel: string;
  representation: ShaderRepresentation | null;
  representationLabel: string;
  executionExplanation: string;
  policyId: string | null;
  policySha256: string | null;
  configuredStage: string | null;
  effectiveStage: string | null;
  bucket: number | null;
  selectedAttemptId: string | null;
  attempts: EngineAttemptView[];
  fallbackFrom: ShaderEngineId | null;
  fallbackFromLabel: string | null;
  fallbackReason: string | null;
  promotionAuthorizationSha256: string | null;
  shadowSubmissionStatus: string | null;
  shadowSubmissionStatusLabel: string | null;
  shadowSubmissionReason: string | null;
  shadowAttemptId: string | null;
}

function nonEmpty(value: string | null | undefined): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function engineLabel(engine: ShaderEngineId | null | undefined): string {
  return engine
    ? ENGINE_LABELS[engine] ?? engine
    : "未返回（兼容旧响应）";
}

export function representationLabel(
  representation: ShaderRepresentation | null | undefined,
): string {
  return representation
    ? REPRESENTATION_LABELS[representation] ?? representation
    : "未返回（兼容旧响应）";
}

export function attemptStatusLabel(status: string | null | undefined): string {
  const normalized = nonEmpty(status);
  return normalized ? ATTEMPT_STATUS_LABELS[normalized] ?? normalized : "状态未知";
}

function executionExplanation(engine: ShaderEngineId | null): string {
  if (engine === "direct_glsl_layerplan_v1") {
    return "本次 GLSL 的执行来源是 Direct Program。LayerPlan 只提供分层、命名和视觉分析建议，不是 GLSL 的执行表示。";
  }
  if (engine === "shader_graph_v1") {
    return "本次 GLSL 的执行来源是 ShaderGraph DSL（ShaderDocument）。LayerPlan 不参与该结果的执行。";
  }
  return "该响应来自 discriminator 上线前的兼容契约，无法仅凭旧字段确认执行来源。";
}

function buildAttemptView(
  attempt: ShaderEngineAttemptSummary,
  selectedAttemptId: string | null,
): EngineAttemptView {
  const attemptId = nonEmpty(attempt.attempt_id);
  const status = nonEmpty(attempt.status);
  return {
    attemptId,
    engine: attempt.engine ?? null,
    engineLabel: engineLabel(attempt.engine),
    representation: attempt.representation ?? null,
    representationLabel: representationLabel(attempt.representation),
    status,
    statusLabel: attemptStatusLabel(status),
    failureCode: nonEmpty(attempt.failure_code),
    selected: attemptId !== null && attemptId === selectedAttemptId,
  };
}

export function buildEngineRunView(
  engine: ShaderEngineId | null | undefined,
  representation: ShaderRepresentation | null | undefined,
  summary: ShaderEngineRunSummary | null | undefined,
): EngineRunView | null {
  // 旧响应三个字段都缺省时不凭 scene/min_pipeline 猜测 engine。
  if (!engine && !representation && !summary) return null;

  const normalizedEngine = engine ?? null;
  const normalizedRepresentation = representation ?? null;
  const selectedAttemptId = nonEmpty(summary?.selected_attempt_id);
  const attemptItems =
    Array.isArray(summary?.attempts) && summary.attempts.length > 0
      ? summary.attempts
      : Array.isArray(summary?.attempt_refs)
        ? summary.attempt_refs
        : [];
  const attempts = attemptItems.map((attempt) =>
    buildAttemptView(attempt, selectedAttemptId),
  );
  const fallbackFrom = summary?.fallback_from ?? null;
  const shadowSubmissionStatus = nonEmpty(summary?.shadow_submission?.status);

  return {
    engine: normalizedEngine,
    engineLabel: engineLabel(normalizedEngine),
    representation: normalizedRepresentation,
    representationLabel: representationLabel(normalizedRepresentation),
    executionExplanation: executionExplanation(normalizedEngine),
    policyId: nonEmpty(summary?.policy_id),
    policySha256: nonEmpty(summary?.policy_sha256),
    configuredStage: nonEmpty(summary?.configured_stage),
    effectiveStage:
      nonEmpty(summary?.effective_stage) ?? nonEmpty(summary?.stage),
    bucket:
      typeof summary?.bucket === "number" && Number.isFinite(summary.bucket)
        ? summary.bucket
        : null,
    selectedAttemptId,
    attempts,
    fallbackFrom,
    fallbackFromLabel: fallbackFrom ? engineLabel(fallbackFrom) : null,
    fallbackReason: nonEmpty(summary?.fallback_reason),
    promotionAuthorizationSha256: nonEmpty(
      summary?.promotion_authorization_sha256,
    ),
    shadowSubmissionStatus,
    shadowSubmissionStatusLabel: shadowSubmissionStatus
      ? attemptStatusLabel(shadowSubmissionStatus)
      : null,
    shadowSubmissionReason: nonEmpty(summary?.shadow_submission?.reason),
    shadowAttemptId: nonEmpty(summary?.shadow_submission?.attempt_id),
  };
}
