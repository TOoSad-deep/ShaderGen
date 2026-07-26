import "./styles/app.css";

import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMinRunProgress,
  generateShader,
  resolveShaderApiUrl,
  ShaderApiError,
  type MinRunProgressEvent,
  type MinRunProgressResponse,
  type MinRunProgressSnapshot,
  type QualityPreset,
  type ShaderApiFailure,
  type ShaderResponse,
} from "./api/shader";
import { FailureDetails } from "./components/FailureDetails";
import { MinRunLivePanel } from "./components/MinRunLivePanel";
import { SceneMvpSummary } from "./components/SceneMvpSummary";
import {
  ShaderPreview,
  type ClientCompatibilityReport,
} from "./components/ShaderPreview";

type RequestStopKind = "user" | "timeout" | "unmount";

interface ActiveGenerationRequest {
  controller: AbortController;
  timeoutId: number;
  stopKind: RequestStopKind | null;
  timeoutMs: number;
}

interface LiveMinRun {
  runId: string;
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot | null;
  status: string;
}

const MIN_RUN_POLL_INTERVAL_MS = 1200;
const MIN_RUN_MAX_POLL_FAILURES = 3;
const DEFAULT_REQUEST_TIMEOUT_MS: Record<QualityPreset, number> = {
  fast: 4 * 60 * 1000,
  balanced: 7 * 60 * 1000,
  high: 12 * 60 * 1000,
  manual: 30 * 60 * 1000,
};

function newClientRunId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "00000000-0000-4000-8000-000000000000".replace(/0/g, () =>
    Math.floor(Math.random() * 16).toString(16),
  );
}

function generationRequestTimeoutMs(qualityPreset: QualityPreset): number {
  const configured = Number(import.meta.env.VITE_GENERATION_REQUEST_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured >= 10_000) return configured;
  return DEFAULT_REQUEST_TIMEOUT_MS[qualityPreset];
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
  const [compatibility, setCompatibility] =
    useState<ClientCompatibilityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [apiFailure, setApiFailure] = useState<ShaderApiFailure | null>(null);
  const [requestStopNotice, setRequestStopNotice] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [copied, setCopied] = useState(false);
  const [liveRun, setLiveRun] = useState<LiveMinRun | null>(null);
  const copyTimerRef = useRef<number | null>(null);
  const activeGenerationRef = useRef<ActiveGenerationRequest | null>(null);

  useEffect(() => () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  useEffect(() => () => {
    if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    const active = activeGenerationRef.current;
    if (active) {
      active.stopKind = "unmount";
      window.clearTimeout(active.timeoutId);
      active.controller.abort();
    }
  }, []);

  function clearRunOutput() {
    setGlsl("");
    setRunResult(null);
    setCompatibility(null);
    setError("");
    setApiFailure(null);
    setRequestStopNotice("");
    setCopied(false);
    setLiveRun(null);
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

    const runId = newClientRunId();
    let pollStopped = false;
    let pollFailures = 0;
    let lastSeq = 0;

    const mergeProgress = (data: MinRunProgressResponse) => {
      lastSeq = Math.max(lastSeq, data.latest_seq ?? 0);
      setLiveRun((current) => {
        const base = current?.runId === runId ? current.events : [];
        const seen = new Set(base.map((event) => event.seq));
        return {
          runId,
          events: [...base, ...data.events.filter((event) => !seen.has(event.seq))],
          snapshot: data.snapshot ?? current?.snapshot ?? null,
          status: data.status,
        };
      });
    };

    const pollProgress = async () => {
      if (pollStopped) return;
      try {
        mergeProgress(await fetchMinRunProgress(runId, lastSeq));
        pollFailures = 0;
      } catch {
        pollFailures += 1;
      }
      if (!pollStopped && pollFailures < MIN_RUN_MAX_POLL_FAILURES) {
        window.setTimeout(() => void pollProgress(), MIN_RUN_POLL_INTERVAL_MS);
      }
    };

    setLiveRun({ runId, events: [], snapshot: null, status: "pending" });
    window.setTimeout(() => void pollProgress(), 500);
    try {
      const result = await generateShader(selectedFile, {
        runId,
        qualityPreset,
        instruction: instruction.trim(),
        signal: controller.signal,
      });
      setRunResult(result);
      setGlsl(result.glsl);
    } catch (reason) {
      if (active.stopKind === "user") {
        setRequestStopNotice(
          "已停止等待本次响应；这里只中止了浏览器请求，服务端任务可能仍在运行。",
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
      pollStopped = true;
      try {
        mergeProgress(await fetchMinRunProgress(runId, lastSeq));
      } catch {
        // 收尾轮询失败不影响主流程。
      }
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

  const serverRenderUrl = runResult?.final_render_url
    ? resolveShaderApiUrl(runResult.final_render_url)
    : null;
  const statusText = loading
    ? "最小管线运行中"
    : requestStopNotice
      ? "已停止等待"
      : error || compatibility?.status === "error"
        ? "需要处理"
        : runResult
          ? "已完成"
          : selectedFile
            ? "准备运行"
            : "等待上传";

  return (
    <main className="app">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>ShaderGen</h1>
            <p>上传参考图，由 scene_mvp 最小管线生成无贴图 fragment shader。</p>
          </div>
          <div className="topbar-actions">
            <a href="/lab">Node Lab</a>
            <span className={`status ${loading ? "is-loading" : ""}`}>{statusText}</span>
            {loading ? (
              <button type="button" className="stop-button" onClick={handleStopWaiting}>
                停止等待
              </button>
            ) : (
              <button type="button" className="run-button" disabled={!selectedFile} onClick={handleRun}>
                {glsl ? "重新运行" : "开始运行"}
              </button>
            )}
          </div>
        </header>

        <section className="run-config" aria-label="生成配置">
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
              <option value="manual">Manual（1000/32/30）</option>
            </select>
            <small className="experimental-note">
              scene_mvp 返回质量指标、预算用量、场景 JSON 与阶段追踪。
            </small>
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

        {requestStopNotice ? <p className="request-stop-notice">{requestStopNotice}</p> : null}
        {apiFailure ? <FailureDetails message={error} failure={apiFailure} /> : null}
        {liveRun ? (
          <MinRunLivePanel
            runId={liveRun.runId}
            referenceUrl={imageUrl}
            events={liveRun.events}
            snapshot={liveRun.snapshot}
            status={liveRun.status}
          />
        ) : null}
        {runResult ? (
          <SceneMvpSummary
            runId={runResult.run_id}
            stopReason={runResult.stop_reason}
            minPipeline={runResult.min_pipeline}
          />
        ) : null}

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
            ) : <div className="empty">{loading ? "正在生成" : selectedFile ? "等待运行" : "等待上传"}</div>}
          </div>

          {serverRenderUrl ? (
            <div className="panel server-render-panel">
              <div className="panel-header"><h2>服务端最终 Render</h2><span>scene_mvp</span></div>
              <img src={serverRenderUrl} alt="服务端最终渲染图" />
            </div>
          ) : null}
        </section>

        <section className="code-panel">
          <div className="panel-header"><h2>GLSL</h2><button type="button" disabled={!glsl} onClick={copyGlsl}>{copied ? "已复制" : "复制"}</button></div>
          {loading ? <p className="hint">正在感知、生成、渲染并评估 GLSL...</p> : null}
          {error && !apiFailure ? <p className="error">{error}</p> : null}
          <pre>{glsl || (selectedFile ? "点击“开始运行”后会在这里显示生成结果。" : "上传图片后会在这里显示生成结果。")}</pre>
        </section>
      </section>
    </main>
  );
}
