import { apiFetch, parseApiError, resolveApiUrl } from "./client";

export type MemoryStatus = "durable" | "ephemeral" | "degraded";
export type QualityPreset = "fast" | "balanced" | "high";
export type GenerationMode = "procedural_v1" | "scene_mvp";

export interface MinPipelineTracePhase {
  phase: string;
  status: string;
  duration_ms?: number | null;
  message?: string | null;
}

// scene_mvp 最小管线的运行摘要；后端字段可缺省，前端全部按可选处理。
export interface MinPipelineSummary {
  mae?: number | null;
  objective_loss?: number | null;
  metric_breakdown?: {
    metric_version?: string;
    global_mae?: number;
    foreground_mae?: number;
    background_mae?: number;
    geometry_mask_loss?: number;
    edge_loss?: number;
    worst_tile_mae?: number;
  } | null;
  template_version?: string | null;
  render_count?: number | null;
  render_budget?: number | null;
  llm_call_count?: number | null;
  llm_budget?: number | null;
  refine_budget?: number | null;
  scene?: unknown;
  trace?: MinPipelineTracePhase[] | null;
  // 质量目标：区分“流程完成”与“质量达标”
  target_mae?: number | null;
  target_loss?: number | null;
  target_reached?: boolean | null;
  // prepared 渲染路径与 prepare 阶段耗时
  renderer_path?: string | null;
  prepare_duration_ms?: number | null;
  // prepared 后复用 uniform 的热渲染统计
  uniform_render_count?: number | null;
  uniform_render_p95_ms?: number | null;
}

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
  generation_mode: GenerationMode;
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
  min_pipeline?: MinPipelineSummary | null;
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
  runId?: string;
  qualityPreset: QualityPreset;
  generationMode?: GenerationMode;
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
  if (options.runId) formData.append("run_id", options.runId);
  formData.append("generation_mode", options.generationMode ?? "procedural_v1");
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

// scene_mvp 运行中单节点进度事件；后端白名单字段，全部按可缺省处理。
export interface MinRunProgressEvent {
  seq: number;
  node: string;
  status: string;
  phase?: string | null;
  elapsed_ms?: number | null;
  duration_ms?: number | null;
  budgets?: Record<string, number> | null;
  counters?: {
    render_count?: number;
    llm_call_count?: number;
    refine_count?: number;
  } | null;
  best?: { mae?: number; loss?: number } | null;
  trace?: Array<Record<string, unknown>> | null;
  next_action?: string | null;
  stop_reason?: string | null;
}

export interface MinRunProgressSnapshot {
  budgets?: Record<string, number> | null;
  counters?: {
    render_count?: number;
    llm_call_count?: number;
    refine_count?: number;
  } | null;
  best?: { mae?: number; loss?: number } | null;
  current_node?: string | null;
  render_seq?: number | null;
}

export type MinRunProgressStatus = "pending" | "running" | "succeeded" | "failed";

export interface MinRunProgressResponse {
  run_id: string;
  status: MinRunProgressStatus;
  generation_mode?: string | null;
  quality_preset?: string | null;
  started_at?: string | null;
  latest_seq: number;
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot;
}

export async function fetchMinRunProgress(
  runId: string,
  after = 0,
): Promise<MinRunProgressResponse> {
  const query = after > 0 ? `?after=${Math.floor(after)}` : "";
  const response = await apiFetch(
    `/api/shader/runs/${encodeURIComponent(runId)}/progress${query}`,
  );
  if (!response.ok) {
    throw await readError(response, "读取运行进度失败。");
  }
  return response.json();
}

export function resolveMinRunRenderUrl(runId: string, renderSeq: number): string {
  return resolveApiUrl(
    `/api/shader/runs/${encodeURIComponent(runId)}/progress/render?seq=${renderSeq}`,
  );
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
