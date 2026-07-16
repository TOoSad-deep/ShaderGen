import { apiFetch, parseApiError, resolveApiUrl } from "./client";

export type NodeLabExecutionMode = "deterministic" | "fixture" | "mock" | "real";
export type NodeLabEffectMode = "preview" | "lab_commit" | "project_commit";

export interface NodeLabHealth {
  status: "ok";
  enabled: true;
  real_model_enabled: boolean;
}

export interface NodeLabNodeDescriptor {
  schema_version: string;
  pipeline_id: string;
  node_id: string;
  category: string;
  summary: string;
  prerequisites: string[];
  side_effects: string[];
  implementation_status: "available" | "partial" | "planned";
  execution_modes: NodeLabExecutionMode[];
  supports_batch: boolean;
  test_profiles: string[];
  benchmark_profiles: string[];
  default_fixture_ids: string[];
  benchmark_metrics: string[];
  cold_start_sensitive: boolean;
  requires_browser: boolean;
  requires_model: boolean;
  source_ref: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  input_examples: NodeLabInputExample[];
}

export interface NodeLabInputExample {
  schema_version: string;
  example_id: string;
  summary: string;
  execution_mode: NodeLabExecutionMode;
  effect_mode: NodeLabEffectMode;
  expected_outcome: "success" | "rejected" | "stopped" | "failed";
  base_step_node_id: string | null;
  fixture_id: string | null;
  inputs: Record<string, unknown>;
  artifact_inputs: Record<string, string>;
}

export interface NodeLabRun {
  schema_version: string;
  pipeline_id: string;
  lab_run_id: string;
  project_id: string | null;
  created_at: string;
  root_state_sha256: string;
}

export interface NodeLabArtifact {
  schema_version: string;
  artifact_id: string;
  lab_run_id: string;
  kind: string;
  content_type: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
}

export interface NodeLabStateDiff {
  added: Record<string, unknown>;
  changed: Record<string, unknown>;
  removed: string[];
}

export interface NodeLabStep {
  schema_version: string;
  pipeline_id: string;
  lab_run_id: string;
  step_id: string;
  base_step_id: string | null;
  node_id: string;
  execution_mode: string;
  execution_status: "completed" | "failed";
  outcome: "success" | "rejected" | "stopped" | "failed";
  input_summary: Record<string, unknown>;
  output: Record<string, unknown>;
  state_diff: NodeLabStateDiff;
  artifacts: NodeLabArtifact[];
  diagnostics: Record<string, unknown>;
  provenance: Record<string, unknown>;
  usage: Record<string, unknown>;
  next_action: string | null;
  duration_ms: number;
  execution_fingerprint: string;
  created_at: string;
}

export interface NodeLabStepSummary {
  schema_version: "node_lab_step_summary_v1";
  lab_run_id: string;
  step_id: string;
  base_step_id: string | null;
  node_id: string;
  execution_mode: string;
  execution_status: "completed" | "failed";
  outcome: "success" | "rejected" | "stopped" | "failed";
  artifact_count: number;
  next_action: string | null;
  duration_ms: number;
  execution_fingerprint: string;
  created_at: string;
}

export interface ExecuteNodeLabStepBody {
  node_id: string;
  execution_mode: NodeLabExecutionMode;
  effect_mode: NodeLabEffectMode;
  preview_only: boolean;
  allow_model_call: boolean;
  base_step_id: string | null;
  fixture_id: string | null;
  mock_response_artifact_id: string | null;
  inputs: Record<string, unknown>;
}

export interface NodeLabErrorDetail {
  status: number;
  code: string;
  message: string;
  stage?: string;
  retryable?: boolean;
}

export class NodeLabApiError extends Error {
  readonly detail: NodeLabErrorDetail;

  constructor(detail: NodeLabErrorDetail) {
    super(detail.message);
    this.name = "NodeLabApiError";
    this.detail = detail;
  }
}

function readText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

async function readError(response: Response): Promise<NodeLabApiError> {
  const parsed = await parseApiError(
    response,
    `Node Lab 请求失败（HTTP ${response.status}）。`,
  );
  return new NodeLabApiError({
    status: parsed.status,
    code: readText(parsed.fields.code) ?? "http_error",
    message: parsed.message,
    stage: readText(parsed.fields.stage),
    retryable:
      typeof parsed.fields.retryable === "boolean"
        ? parsed.fields.retryable
        : undefined,
  });
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) throw await readError(response);
  return response.json() as Promise<T>;
}

export function resolveNodeLabArtifactUrl(labRunId: string, artifactId: string): string {
  return resolveApiUrl(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/artifacts/${encodeURIComponent(artifactId)}`,
  );
}

export async function getNodeLabHealth(): Promise<NodeLabHealth> {
  return requestJson<NodeLabHealth>("/api/lab/v1/health");
}

export async function listNodeLabNodes(): Promise<NodeLabNodeDescriptor[]> {
  return requestJson<NodeLabNodeDescriptor[]>("/api/lab/v1/nodes");
}

export async function createNodeLabRun(
  projectId: string | null,
  initialState: Record<string, unknown>,
): Promise<NodeLabRun> {
  return requestJson<NodeLabRun>("/api/lab/v1/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId || null, initial_state: initialState }),
  });
}

export async function getNodeLabRun(labRunId: string): Promise<NodeLabRun> {
  return requestJson<NodeLabRun>(`/api/lab/v1/runs/${encodeURIComponent(labRunId)}`);
}

export async function listNodeLabSteps(labRunId: string): Promise<NodeLabStepSummary[]> {
  const response = await requestJson<{
    lab_run_id: string;
    step_ids: string[];
    steps: NodeLabStepSummary[];
  }>(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/steps`,
  );
  return response.steps;
}

export async function getNodeLabStep(
  labRunId: string,
  stepId: string,
): Promise<NodeLabStep> {
  return requestJson<NodeLabStep>(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/steps/${encodeURIComponent(stepId)}`,
  );
}

export function summarizeNodeLabStep(step: NodeLabStep): NodeLabStepSummary {
  return {
    schema_version: "node_lab_step_summary_v1",
    lab_run_id: step.lab_run_id,
    step_id: step.step_id,
    base_step_id: step.base_step_id,
    node_id: step.node_id,
    execution_mode: step.execution_mode,
    execution_status: step.execution_status,
    outcome: step.outcome,
    artifact_count: step.artifacts.length,
    next_action: step.next_action,
    duration_ms: step.duration_ms,
    execution_fingerprint: step.execution_fingerprint,
    created_at: step.created_at,
  };
}

export async function listNodeLabArtifacts(labRunId: string): Promise<NodeLabArtifact[]> {
  const response = await requestJson<{ lab_run_id: string; artifacts: NodeLabArtifact[] }>(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/artifacts`,
  );
  return response.artifacts;
}

export async function executeNodeLabStep(
  labRunId: string,
  body: ExecuteNodeLabStepBody,
): Promise<NodeLabStep> {
  return requestJson<NodeLabStep>(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/steps`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function uploadNodeLabArtifact(
  labRunId: string,
  file: File,
  kind: string,
): Promise<NodeLabArtifact> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("kind", kind);
  const response = await apiFetch(
    `/api/lab/v1/runs/${encodeURIComponent(labRunId)}/artifacts`,
    { method: "POST", body: formData },
  );
  if (!response.ok) throw await readError(response);
  return response.json() as Promise<NodeLabArtifact>;
}
