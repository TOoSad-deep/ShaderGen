import { apiFetch, parseApiError, resolveApiUrl } from "./client";

export type MemoryStatus = "durable" | "ephemeral" | "degraded";
export type QualityPreset = "fast" | "balanced" | "high";

export interface ShaderScore {
  metric_version: string;
  total_loss: number;
  global_rmse: number;
  global_mae: number;
  edge_loss: number;
  geometry_loss: number | null;
  representative_pixel_loss: number;
  roi_losses: Record<string, number>;
  protected_region_losses: Record<string, number>;
  effective_weights: Record<string, number>;
  diagnostics: string[];
}

export interface ShaderResponse {
  project_id: string;
  run_id: string;
  glsl: string;
  memory_status: MemoryStatus;
  generation_mode: "procedural_v1";
  quality_preset?: QualityPreset | null;
  iterations: number;
  stop_reason?: string | null;
  best_candidate_id?: string | null;
  render_width?: number | null;
  render_height?: number | null;
  final_render_url?: string | null;
  metrics_url?: string | null;
  manifest_url?: string | null;
  score?: ShaderScore | null;
  unscored_fallback?: boolean;
  review?: ShaderReview | null;
}

export interface ShaderReview {
  evaluation: string;
  suggestions: string[];
}

export interface ShaderApiFailure {
  status: number;
  code?: string;
  runId?: string;
  stage?: string;
  retryable?: boolean;
  stopReason?: string;
}

export class ShaderApiError extends Error {
  readonly failure: ShaderApiFailure;

  constructor(message: string, failure: ShaderApiFailure) {
    super(message);
    this.name = "ShaderApiError";
    this.failure = failure;
  }
}

export function resolveShaderApiUrl(path: string): string {
  return resolveApiUrl(path);
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

async function readError(response: Response, fallback: string): Promise<ShaderApiError> {
  const parsed = await parseApiError(response, fallback);
  return new ShaderApiError(parsed.message, {
    status: parsed.status,
    code: readString(parsed.fields.code),
    runId: readString(parsed.fields.run_id),
    stage: readString(parsed.fields.stage),
    retryable:
      typeof parsed.fields.retryable === "boolean"
        ? parsed.fields.retryable
        : undefined,
    stopReason: readString(parsed.fields.stop_reason),
  });
}

export interface GenerateShaderOptions {
  projectId?: string;
  qualityPreset: QualityPreset;
  instruction: string;
  signal?: AbortSignal;
}

export async function generateShader(
  file: File,
  options: GenerateShaderOptions,
): Promise<ShaderResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (options.projectId) formData.append("project_id", options.projectId);
  formData.append("generation_mode", "procedural_v1");
  formData.append("quality_preset", options.qualityPreset);
  formData.append("instruction", options.instruction);

  const response = await apiFetch("/api/shader/generate", {
    method: "POST",
    body: formData,
    signal: options.signal,
  });

  if (!response.ok) {
    throw await readError(response, "生成 GLSL 失败。");
  }

  return response.json();
}

export async function clearProjectMemory(projectId: string): Promise<void> {
  const response = await apiFetch(
    `/api/shader/projects/${encodeURIComponent(projectId)}/memory`,
    { method: "DELETE" },
  );

  if (!response.ok) {
    throw await readError(response, "清除项目记忆失败。");
  }
}
