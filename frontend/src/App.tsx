import "./styles/app.css";

import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  clearProjectMemory,
  generateShader,
  resolveShaderApiUrl,
  ShaderApiError,
  type MemoryStatus,
  type QualityPreset,
  type ShaderApiFailure,
  type ShaderResponse,
} from "./api/shader";
import { FailureDetails } from "./components/FailureDetails";
import { RunProgress } from "./components/RunProgress";
import { ScoreSummary } from "./components/ScoreSummary";
import {
  ShaderPreview,
  type ClientCompatibilityReport,
} from "./components/ShaderPreview";

const CURRENT_PROJECT_KEY = "shadergen.currentProjectId";
const RECENT_PROJECTS_KEY = "shadergen.recentProjects";

interface RecentProject {
  id: string;
  lastUsedAt: string;
}

type RequestStopKind = "user" | "timeout" | "unmount";

interface ActiveGenerationRequest {
  controller: AbortController;
  timeoutId: number;
  stopKind: RequestStopKind | null;
  timeoutMs: number;
}

const DEFAULT_REQUEST_TIMEOUT_MS: Record<QualityPreset, number> = {
  fast: 4 * 60 * 1000,
  balanced: 7 * 60 * 1000,
  high: 12 * 60 * 1000,
  ultra: 42 * 60 * 1000,
};

function generationRequestTimeoutMs(qualityPreset: QualityPreset): number {
  const configured = Number(import.meta.env.VITE_GENERATION_REQUEST_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured >= 10_000) return configured;
  return DEFAULT_REQUEST_TIMEOUT_MS[qualityPreset];
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
  const [qualityPreset, setQualityPreset] = useState<QualityPreset>("balanced");
  const [instruction, setInstruction] = useState("");
  const [runResult, setRunResult] = useState<ShaderResponse | null>(null);
  const [compatibility, setCompatibility] = useState<ClientCompatibilityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [apiFailure, setApiFailure] = useState<ShaderApiFailure | null>(null);
  const [requestStopNotice, setRequestStopNotice] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [copied, setCopied] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(() =>
    localStorage.getItem(CURRENT_PROJECT_KEY),
  );
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>(loadRecentProjects);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const [projectMessage, setProjectMessage] = useState("");
  const copyTimerRef = useRef<number | null>(null);
  const activeGenerationRef = useRef<ActiveGenerationRequest | null>(null);

  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [imageUrl]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
      const active = activeGenerationRef.current;
      if (active) {
        active.stopKind = "unmount";
        window.clearTimeout(active.timeoutId);
        active.controller.abort();
      }
    };
  }, []);

  const rememberProject = useCallback((id: string) => {
    setRecentProjects((current) => {
      const next = [
        { id, lastUsedAt: new Date().toISOString() },
        ...current.filter((project) => project.id !== id),
      ].slice(0, 10);
      localStorage.setItem(CURRENT_PROJECT_KEY, id);
      localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(next));
      return next;
    });
    setProjectId(id);
  }, []);

  function clearRunOutput() {
    setGlsl("");
    setRunResult(null);
    setCompatibility(null);
    setError("");
    setApiFailure(null);
    setRequestStopNotice("");
    setCopied(false);
  }

  async function handleFile(file: File) {
    if (loading) return;
    if (!file.type.startsWith("image/")) {
      setError("请上传图片文件。");
      return;
    }

    setImageUrl(URL.createObjectURL(file));
    setSelectedFile(file);
    setFileInfo(`${file.name} · ${formatBytes(file.size)}`);
    clearRunOutput();
  }

  function resetWorkspace() {
    setImageUrl(null);
    setSelectedFile(null);
    setFileInfo("");
    clearRunOutput();
    setMemoryStatus(null);
  }

  async function handleRun() {
    if (!selectedFile || loading || activeGenerationRef.current) return;

    clearRunOutput();
    setLoading(true);
    const controller = new AbortController();
    const timeoutMs = generationRequestTimeoutMs(qualityPreset);
    const active: ActiveGenerationRequest = {
      controller,
      timeoutId: 0,
      stopKind: null,
      timeoutMs,
    };
    active.timeoutId = window.setTimeout(() => {
      if (activeGenerationRef.current !== active) return;
      active.stopKind = "timeout";
      controller.abort();
    }, timeoutMs);
    activeGenerationRef.current = active;

    try {
      const result = await generateShader(selectedFile, {
        projectId: projectId ?? undefined,
        qualityPreset,
        instruction: instruction.trim(),
        signal: controller.signal,
      });
      rememberProject(result.project_id);
      setMemoryStatus(result.memory_status);
      setProjectMessage("");
      setRunResult(result);
      setGlsl(result.glsl);
    } catch (reason) {
      if (active.stopKind === "user") {
        setRequestStopNotice(
          "已停止等待本次响应；这里只中止了浏览器请求，没有向服务端发送取消指令。服务端任务可能仍在运行，请先查看后端日志或稍后再重试。",
        );
      } else if (active.stopKind === "timeout") {
        setRequestStopNotice(
          `浏览器等待超过 ${Math.round(active.timeoutMs / 60_000)} 分钟，已停止等待响应；这不代表服务端任务已取消。`,
        );
      } else if (active.stopKind !== "unmount") {
        if (reason instanceof ShaderApiError) setApiFailure(reason.failure);
        setError(reason instanceof Error ? reason.message : "生成失败。");
      }
    } finally {
      window.clearTimeout(active.timeoutId);
      if (activeGenerationRef.current === active) {
        activeGenerationRef.current = null;
        setLoading(false);
      }
    }
  }

  function handleStopWaiting() {
    const active = activeGenerationRef.current;
    if (!active || active.stopKind) return;
    active.stopKind = "user";
    active.controller.abort();
  }

  const handleCompatibility = useCallback((report: ClientCompatibilityReport) => {
    setCompatibility(report);
  }, []);

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
    } catch (reason) {
      setProjectMessage(reason instanceof Error ? reason.message : "清除项目记忆失败。");
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

  const review = runResult?.review ?? null;
  const serverRenderUrl = runResult?.final_render_url
    ? resolveShaderApiUrl(runResult.final_render_url)
    : null;
  const statusText = loading
    ? "闭环运行中"
    : requestStopNotice
      ? "已停止等待"
      : error || compatibility?.status === "error"
        ? "需要处理"
        : runResult
          ? "已完成"
          : selectedFile
            ? "准备运行"
            : "等待上传";
  const runText = glsl ? "重新运行" : "开始运行";
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
            <p>上传参考图，生成可评估、可复核的无贴图 fragment shader。</p>
          </div>
          <div className="topbar-actions">
            <span className={`status ${loading ? "is-loading" : ""}`}>{statusText}</span>
            {loading ? (
              <button type="button" className="stop-button" onClick={handleStopWaiting}>
                停止等待
              </button>
            ) : (
              <button type="button" className="run-button" disabled={!selectedFile} onClick={handleRun}>
                {runText}
              </button>
            )}
          </div>
        </header>

        <section className="run-config" aria-label="生成配置">
          <label>
            <span>生成模式</span>
            <strong>程序化闭环 V1</strong>
            <small className="experimental-note">
              实验功能：当前质量门禁未通过，可能超时或无法生成可运行 Shader。
            </small>
          </label>
          <label>
            <span>质量档位</span>
            <select
              aria-label="质量档位"
              value={qualityPreset}
              disabled={loading}
              onChange={(event) => setQualityPreset(event.target.value as QualityPreset)}
            >
              <option value="fast">Fast</option>
              <option value="balanced">Balanced</option>
              <option value="high">High</option>
              <option value="ultra">Ultra</option>
            </select>
          </label>
          <label className="instruction-field">
            <span>补充约束</span>
            <textarea
              aria-label="补充约束"
              value={instruction}
              maxLength={2000}
              disabled={loading}
              placeholder="例如：保留纯白背景，重点复刻左上高光。"
              onChange={(event) => setInstruction(event.target.value)}
            />
          </label>
        </section>

        <section className="project-bar" aria-label="项目记忆">
          <div className="project-current">
            <strong>项目记忆</strong>
            <span title={projectId ?? ""}>{projectId ? projectId : "下一次运行时创建"}</span>
          </div>
          <div className="project-actions">
            <select aria-label="最近项目" value={projectId ?? ""} onChange={(event) => restoreProject(event.target.value)}>
              <option value="">最近项目</option>
              {recentProjects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.id.slice(0, 8)} · {new Date(project.lastUsedAt).toLocaleString()}
                </option>
              ))}
            </select>
            <button type="button" onClick={handleNewProject} disabled={loading}>新建项目</button>
            <button type="button" onClick={() => void handleClearMemory()} disabled={!projectId || loading}>
              清除记忆
            </button>
          </div>
        </section>
        {memoryStatus === "ephemeral" ? <p className="memory-warning">当前使用临时记忆，后端重启后会丢失。</p> : null}
        {memoryStatus === "degraded" ? <p className="memory-warning is-error">长期记忆本次降级，结果仍可使用但未保证持久化。</p> : null}
        {projectMessage ? <p className="project-message">{projectMessage}</p> : null}
        {requestStopNotice ? <p className="request-stop-notice">{requestStopNotice}</p> : null}
        {apiFailure ? <FailureDetails message={error} failure={apiFailure} /> : null}

        <RunProgress
          loading={loading}
          result={runResult}
          compatibility={compatibility}
        />

        <section className={`panels ${serverRenderUrl ? "has-server-render" : ""}`}>
          <div className="panel">
            <div className="panel-header"><h2>原图</h2>{fileInfo ? <span>{fileInfo}</span> : null}</div>
            <label
              className={`dropzone ${dragActive ? "is-dragging" : ""} ${loading ? "is-disabled" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
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
              {imageUrl ? <img src={imageUrl} alt="上传的原图" /> : <span>拖拽图片到这里<small>或点击上传</small></span>}
            </label>
          </div>

          <div className="panel">
            <div className="panel-header"><h2>客户端 WebGL1</h2>{loading ? <span>正在生成...</span> : null}</div>
            {glsl ? (
              <ShaderPreview
                imageUrl={imageUrl}
                glsl={glsl}
                renderWidth={runResult?.render_width}
                renderHeight={runResult?.render_height}
                serverRenderUrl={serverRenderUrl}
                onCompatibility={handleCompatibility}
              />
            ) : <div className="empty">{previewEmptyText}</div>}
          </div>

          {serverRenderUrl ? (
            <div className="panel server-render-panel">
              <div className="panel-header">
                <h2>服务端最终 Render</h2>
                <span>{runResult?.unscored_fallback ? "WebGL fallback" : "current_best"}</span>
              </div>
              <img src={serverRenderUrl} alt="服务端最终渲染图" />
            </div>
          ) : null}
        </section>

        <ScoreSummary score={runResult?.score} />

        <section className="code-panel">
          <div className="panel-header"><h2>GLSL</h2><button type="button" disabled={!glsl} onClick={copyGlsl}>{copied ? "已复制" : "复制"}</button></div>
          {loading ? <p className="hint">正在分析、编译、渲染并按评分自动修订 GLSL...</p> : null}
          {error && !apiFailure ? <p className="error">{error}</p> : null}
          <pre>{glsl || codePlaceholder}</pre>
        </section>

        {glsl || review ? (
          <section className="review-panel">
            <div className="panel-header">
              <h2>自动闭环 Review</h2>
            </div>
            {review ? (
              <div className="review-body">
                <p>{review.evaluation}</p>
                {review.suggestions.length ? <ul>{review.suggestions.map((suggestion, index) => <li key={`${index}-${suggestion}`}>{suggestion}</li>)}</ul> : null}
              </div>
            ) : <p className="hint">本次在进入 Critic 前已满足停止条件。</p>}
          </section>
        ) : null}
      </section>
    </main>
  );
}
