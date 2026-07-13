export type MemoryStatus = "durable" | "ephemeral" | "degraded";

export interface ShaderResponse {
  project_id: string;
  glsl: string;
  memory_status: MemoryStatus;
}

export interface ShaderReview {
  evaluation: string;
  suggestions: string[];
}

export interface ShaderReviewResponse {
  project_id: string;
  review: ShaderReview;
  memory_status: MemoryStatus;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8088";

async function readError(response: Response, fallback: string) {
  const body = await response.text();
  let message = body;

  try {
    const parsed = JSON.parse(body) as { detail?: string };
    message = parsed.detail ?? body;
  } catch {
    message = body;
  }

  return message || fallback;
}

export async function generateShader(file: File, projectId?: string): Promise<ShaderResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (projectId) formData.append("project_id", projectId);

  const response = await fetch(`${API_BASE_URL}/api/shader/generate`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await readError(response, "生成 GLSL 失败。"));
  }

  return response.json();
}

export async function reviewShader(
  originalFile: File,
  renderedImage: Blob,
  glsl: string,
  projectId: string,
): Promise<ShaderReviewResponse> {
  const formData = new FormData();
  formData.append("original_file", originalFile);
  formData.append("rendered_file", renderedImage, "rendered.png");
  formData.append("glsl", glsl);
  formData.append("project_id", projectId);

  const response = await fetch(`${API_BASE_URL}/api/shader/review`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await readError(response, "评审渲染图失败。"));
  }

  return response.json();
}

export async function clearProjectMemory(projectId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/shader/projects/${projectId}/memory`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw new Error(await readError(response, "清除项目记忆失败。"));
  }
}
