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

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8088";

export function resolveShaderApiUrl(path: string): string {
  return new URL(path, `${API_BASE_URL}/`).toString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

async function readError(response: Response, fallback: string): Promise<ShaderApiError> {
  const body = await response.text();
  let message = body.trim() || fallback;
  let fields: Record<string, unknown> = {};

  try {
    const parsed: unknown = JSON.parse(body);
    if (isRecord(parsed)) {
      const detail = parsed.detail;
      const nestedFields = isRecord(detail)
        ? detail
        : isRecord(parsed.error)
          ? parsed.error
          : {};
      fields = { ...parsed, ...nestedFields };
      message =
        readString(nestedFields.message) ??
        readString(parsed.message) ??
        readString(detail) ??
        message;
    }
  } catch {
    // 服务端也可能直接返回纯文本；保留原消息便于定位。
  }

  return new ShaderApiError(message, {
    status: response.status,
    code: readString(fields.code),
    runId: readString(fields.run_id),
    stage: readString(fields.stage),
    retryable: typeof fields.retryable === "boolean" ? fields.retryable : undefined,
    stopReason: readString(fields.stop_reason),
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

  const response = await fetch(`${API_BASE_URL}/api/shader/generate`, {
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
  const response = await fetch(`${API_BASE_URL}/api/shader/projects/${projectId}/memory`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw await readError(response, "清除项目记忆失败。");
  }
}
