import "./styles/app.css";

import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  clearProjectMemory,
  generateShader,
  reviewShader,
  type MemoryStatus,
  type ShaderReview,
} from "./api/shader";
import { ShaderPreview } from "./components/ShaderPreview";

const CURRENT_PROJECT_KEY = "shadergen.currentProjectId";
const RECENT_PROJECTS_KEY = "shadergen.recentProjects";

interface RecentProject {
  id: string;
  lastUsedAt: string;
}

function loadRecentProjects(): RecentProject[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_PROJECTS_KEY) ?? "[]") as unknown;
    if (!Array.isArray(value)) return [];
    return value
      .filter(
        (item): item is RecentProject =>
          typeof item === "object" &&
          item !== null &&
          typeof (item as RecentProject).id === "string" &&
          typeof (item as RecentProject).lastUsedAt === "string",
      )
      .slice(0, 10);
  } catch {
    return [];
  }
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function App() {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileInfo, setFileInfo] = useState("");
  const [glsl, setGlsl] = useState("");
  const [review, setReview] = useState<ShaderReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [copied, setCopied] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(() =>
    localStorage.getItem(CURRENT_PROJECT_KEY),
  );
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>(loadRecentProjects);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const [projectMessage, setProjectMessage] = useState("");
  const copyTimerRef = useRef<number | null>(null);
  const reviewRequestRef = useRef(0);

  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [imageUrl]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    };
  }, []);

  async function handleFile(file: File) {
    if (loading) return;

    if (!file.type.startsWith("image/")) {
      setError("请上传图片文件。");
      return;
    }

    setImageUrl(URL.createObjectURL(file));
    setSelectedFile(file);
    setFileInfo(`${file.name} · ${formatBytes(file.size)}`);
    setGlsl("");
    setReview(null);
    setReviewError("");
    setReviewLoading(false);
    setError("");
    setCopied(false);
    reviewRequestRef.current += 1;
  }

  function rememberProject(id: string) {
    const next = [
      { id, lastUsedAt: new Date().toISOString() },
      ...recentProjects.filter((project) => project.id !== id),
    ].slice(0, 10);
    localStorage.setItem(CURRENT_PROJECT_KEY, id);
    localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(next));
    setProjectId(id);
    setRecentProjects(next);
  }

  function resetWorkspace() {
    setImageUrl(null);
    setSelectedFile(null);
    setFileInfo("");
    setGlsl("");
    setReview(null);
    setReviewError("");
    setReviewLoading(false);
    setError("");
    setCopied(false);
    setMemoryStatus(null);
    reviewRequestRef.current += 1;
  }

  async function handleRun() {
    if (!selectedFile || loading) return;

    setGlsl("");
    setReview(null);
    setReviewError("");
    setReviewLoading(false);
    setError("");
    setCopied(false);
    setLoading(true);
    reviewRequestRef.current += 1;

    try {
      const result = await generateShader(selectedFile, projectId ?? undefined);
      rememberProject(result.project_id);
      setMemoryStatus(result.memory_status);
      setProjectMessage("");
      setGlsl(result.glsl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败。");
    } finally {
      setLoading(false);
    }
  }

  const handleRendered = useCallback(
    async (renderedImage: Blob) => {
      if (!selectedFile || !glsl || !projectId) return;

      const requestId = reviewRequestRef.current + 1;
      reviewRequestRef.current = requestId;
      setReview(null);
      setReviewError("");
      setReviewLoading(true);

      try {
        const result = await reviewShader(selectedFile, renderedImage, glsl, projectId);
        if (reviewRequestRef.current === requestId) {
          setReview(result.review);
          setMemoryStatus(result.memory_status);
          rememberProject(result.project_id);
        }
      } catch (err) {
        if (reviewRequestRef.current === requestId) {
          setReviewError(err instanceof Error ? err.message : "评审失败。");
        }
      } finally {
        if (reviewRequestRef.current === requestId) setReviewLoading(false);
      }
    },
    [selectedFile, glsl, projectId, recentProjects],
  );

  function handleNewProject() {
    if (
      projectId &&
      !window.confirm("新建项目只会切换 project_id，旧项目记忆会继续保留在最近项目中。是否继续？")
    ) {
      return;
    }
    localStorage.removeItem(CURRENT_PROJECT_KEY);
    setProjectId(null);
    setProjectMessage("已准备新项目，下一次运行会创建新的 project_id。 ");
    resetWorkspace();
  }

  async function handleClearMemory() {
    if (!projectId) return;
    if (!window.confirm("确认清除当前项目的任务记忆和长期记忆？过程审计记录不会删除。")) return;
    try {
      await clearProjectMemory(projectId);
      const next = recentProjects.filter((project) => project.id !== projectId);
      localStorage.removeItem(CURRENT_PROJECT_KEY);
      localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(next));
      setRecentProjects(next);
      setProjectId(null);
      resetWorkspace();
      setProjectMessage("当前项目记忆已清除。 ");
    } catch (err) {
      setProjectMessage(err instanceof Error ? err.message : "清除项目记忆失败。");
    }
  }

  function restoreProject(id: string) {
    if (!id) return;
    rememberProject(id);
    resetWorkspace();
    setProjectMessage("已恢复项目记忆范围，请上传图片继续生成。 ");
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);

    const file = event.dataTransfer.files[0];
    if (file) void handleFile(file);
  }

  async function copyGlsl() {
    if (!glsl || !navigator.clipboard) return;

    try {
      await navigator.clipboard.writeText(glsl);
      setCopied(true);
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("复制失败，请手动选择代码复制。");
    }
  }

  const statusText = loading
    ? "生成中"
    : reviewLoading
      ? "评审中"
      : error || reviewError
        ? "需要处理"
        : review
          ? "已评审"
          : glsl
            ? "已生成"
            : selectedFile
              ? "准备运行"
              : "等待上传";
  const runText = loading ? "运行中..." : glsl ? "重新运行" : "开始运行";
  const previewEmptyText = loading ? "正在生成" : selectedFile ? "等待运行" : "等待上传";
  const codePlaceholder = selectedFile
    ? "点击“开始运行”后会在这里显示生成的 fragment shader。"
    : "上传图片后会在这里显示生成的 fragment shader。";

  return (
    <main className="app">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>ShaderGen</h1>
            <p>上传参考图，生成可预览的 fragment shader。</p>
          </div>
          <div className="topbar-actions">
            <span className={`status ${loading ? "is-loading" : ""}`}>{statusText}</span>
            <button type="button" className="run-button" disabled={!selectedFile || loading} onClick={handleRun}>
              {runText}
            </button>
          </div>
        </header>

        <section className="project-bar" aria-label="项目记忆">
          <div className="project-current">
            <strong>项目记忆</strong>
            <span title={projectId ?? ""}>{projectId ? projectId : "下一次运行时创建"}</span>
          </div>
          <div className="project-actions">
            <select
              aria-label="最近项目"
              value={projectId ?? ""}
              onChange={(event) => restoreProject(event.target.value)}
            >
              <option value="">最近项目</option>
              {recentProjects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.id.slice(0, 8)} · {new Date(project.lastUsedAt).toLocaleString()}
                </option>
              ))}
            </select>
            <button type="button" onClick={handleNewProject} disabled={loading || reviewLoading}>
              新建项目
            </button>
            <button
              type="button"
              onClick={() => void handleClearMemory()}
              disabled={!projectId || loading || reviewLoading}
            >
              清除记忆
            </button>
          </div>
        </section>
        {memoryStatus === "ephemeral" ? (
          <p className="memory-warning">当前使用临时记忆，后端重启后会丢失。</p>
        ) : null}
        {memoryStatus === "degraded" ? (
          <p className="memory-warning is-error">长期记忆本次降级，结果仍可使用但未保证持久化。</p>
        ) : null}
        {projectMessage ? <p className="project-message">{projectMessage}</p> : null}

        <section className="panels">
          <div className="panel">
            <div className="panel-header">
              <h2>原图</h2>
              {fileInfo ? <span>{fileInfo}</span> : null}
            </div>
            <label
              className={`dropzone ${dragActive ? "is-dragging" : ""} ${loading ? "is-disabled" : ""}`}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
            >
              <input
                type="file"
                accept="image/*"
                disabled={loading}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleFile(file);
                  event.currentTarget.value = "";
                }}
              />
              {imageUrl ? (
                <img src={imageUrl} alt="上传的原图" />
              ) : (
                <span>
                  拖拽图片到这里
                  <small>或点击上传</small>
                </span>
              )}
            </label>
          </div>

          <div className="panel">
            <div className="panel-header">
              <h2>WebGL 预览</h2>
              {loading ? <span>正在生成...</span> : null}
            </div>
            {glsl ? (
              <ShaderPreview imageUrl={imageUrl} glsl={glsl} onRendered={handleRendered} />
            ) : (
              <div className="empty">{previewEmptyText}</div>
            )}
          </div>
        </section>

        <section className="code-panel">
          <div className="panel-header">
            <h2>GLSL</h2>
            <button type="button" disabled={!glsl} onClick={copyGlsl}>
              {copied ? "已复制" : "复制"}
            </button>
          </div>
          {loading ? <p className="hint">正在分析图片并生成 GLSL...</p> : null}
          {error ? <p className="error">{error}</p> : null}
          <pre>{glsl || codePlaceholder}</pre>
        </section>

        {glsl || reviewLoading || review ? (
          <section className="review-panel">
            <div className="panel-header">
              <h2>Review</h2>
              {reviewLoading ? <span>正在评审...</span> : null}
            </div>
            {reviewLoading ? <p className="hint">正在评审渲染图...</p> : null}
            {reviewError ? <p className="error">{reviewError}</p> : null}
            {review ? (
              <div className="review-body">
                <p>{review.evaluation}</p>
                {review.suggestions.length ? (
                  <ul>
                    {review.suggestions.map((suggestion, index) => (
                      <li key={`${index}-${suggestion}`}>{suggestion}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}
      </section>
    </main>
  );
}
