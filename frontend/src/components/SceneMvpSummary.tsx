import type { MinPipelineSummary } from "../api/shader";

interface SceneMvpSummaryProps {
  runId: string;
  stopReason?: string | null;
  minPipeline?: MinPipelineSummary | null;
}

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
}

function formatMae(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function formatMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)} ms`
    : "—";
}

function formatSceneJson(scene: unknown): string {
  if (scene === undefined || scene === null) return "（本次响应未返回 scene）";
  if (typeof scene === "string") return scene;
  try {
    return JSON.stringify(scene, null, 2);
  } catch {
    return String(scene);
  }
}

function shortHash(value: string | null | undefined): string {
  return typeof value === "string" && value.length > 12 ? value.slice(0, 12) : value || "—";
}

export function SceneMvpSummary({ runId, stopReason, minPipeline }: SceneMvpSummaryProps) {
  const trace = Array.isArray(minPipeline?.trace) ? minPipeline.trace : [];
  // 质量达标只来自后端 target_reached；缺省（旧响应）时不展示结论，避免误报。
  const targetReached =
    typeof minPipeline?.target_reached === "boolean" ? minPipeline.target_reached : null;
  const rendererPath =
    typeof minPipeline?.renderer_path === "string" && minPipeline.renderer_path.trim()
      ? minPipeline.renderer_path
      : null;
  const graphShadow = minPipeline?.shader_graph_shadow;
  const productGraph =
    typeof minPipeline?.scene === "object" &&
    minPipeline.scene !== null &&
    (minPipeline.scene as Record<string, unknown>).schema_version === "shader_graph_v1"
      ? (minPipeline.scene as {
          schema_version: string;
          layers?: Array<Record<string, unknown>>;
        })
      : null;
  const productLayers = Array.isArray(productGraph?.layers) ? productGraph.layers : [];
  const graphLayers = Array.isArray(graphShadow?.shader_graph?.layers)
    ? graphShadow.shader_graph.layers
    : [];
  return (
    <section className="scene-mvp-panel" aria-label="scene_mvp 运行摘要">
      <div className="panel-header">
        <h2>scene_mvp 最小管线</h2>
        <span>run_id: {runId}</span>
      </div>
      {targetReached !== null ? (
        <p className={`target-status ${targetReached ? "is-reached" : "is-missed"}`}>
          {targetReached ? "质量达标" : "流程完成，质量未达标"}
        </p>
      ) : null}
      <div className="score-grid">
        <div className="score-primary">
          <span>综合损失</span>
          <strong>{formatMae(minPipeline?.objective_loss)}</strong>
        </div>
        <div>
          <span>目标损失</span>
          <strong>{formatMae(minPipeline?.target_loss)}</strong>
        </div>
        <div>
          <span>整图 MAE</span>
          <strong>{formatMae(minPipeline?.mae)}</strong>
        </div>
        <div>
          <span>前景 MAE</span>
          <strong>{formatMae(minPipeline?.metric_breakdown?.foreground_mae)}</strong>
        </div>
        <div>
          <span>背景 / 最坏区域 MAE</span>
          <strong>
            {formatMae(minPipeline?.metric_breakdown?.background_mae)} /{" "}
            {formatMae(minPipeline?.metric_breakdown?.worst_tile_mae)}
          </strong>
        </div>
        <div>
          <span>几何 / 边缘损失</span>
          <strong>
            {formatMae(minPipeline?.metric_breakdown?.geometry_mask_loss)} /{" "}
            {formatMae(minPipeline?.metric_breakdown?.edge_loss)}
          </strong>
        </div>
        <div>
          <span>渲染次数</span>
          <strong>
            {formatCount(minPipeline?.render_count)} /{" "}
            {formatCount(minPipeline?.render_budget)}
          </strong>
        </div>
        <div>
          <span>LLM 调用次数</span>
          <strong>
            {formatCount(minPipeline?.llm_call_count)} /{" "}
            {formatCount(minPipeline?.llm_budget)}
          </strong>
        </div>
        <div>
          <span>停止原因</span>
          <strong>{stopReason || "—"}</strong>
        </div>
      </div>
      <div className="score-grid">
        <div>
          <span>prepare 耗时</span>
          <strong>{formatMs(minPipeline?.prepare_duration_ms)}</strong>
        </div>
        <div>
          <span>uniform 热渲染次数</span>
          <strong>{formatCount(minPipeline?.uniform_render_count)}</strong>
        </div>
        <div>
          <span>uniform 热渲染 P95</span>
          <strong>{formatMs(minPipeline?.uniform_render_p95_ms)}</strong>
        </div>
      </div>
      {rendererPath ? (
        <p className="renderer-path" title={rendererPath}>
          prepared 渲染路径：{rendererPath}
          {minPipeline?.template_version
            ? ` · 模板：${minPipeline.template_version}`
            : ""}
        </p>
      ) : null}
      {productGraph ? (
        <section className="shader-graph-shadow" aria-label="ShaderGraph 产品摘要">
          <div className="panel-header">
            <h3>ShaderGraph 产品文档</h3>
            <span>{productGraph.schema_version}</span>
          </div>
          <p>
            当前 GLSL、渲染结果与 current_best 均由该 typed ShaderGraph 编译产生；
            下方 GLSL 与服务端最终 Render 是运行结束时冻结的 current_best 候选产物。
          </p>
          <details className="scene-mvp-details">
            <summary>图层检查器（{productLayers.length}）</summary>
            <ol>
              {productLayers.map((layer, index) => (
                <li key={String(layer.id || index)}>
                  <strong>{String(layer.id || `layer-${index + 1}`)}</strong>
                  <span>
                    {String(
                      (layer.shape as Record<string, unknown> | undefined)?.kind ||
                        "unknown",
                    )}
                  </span>
                  <span>
                    {String(
                      (layer.fill as Record<string, unknown> | undefined)?.kind ||
                        "unknown",
                    )}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        </section>
      ) : null}
      {graphShadow ? (
        <section className="shader-graph-shadow" aria-label="ShaderGraph shadow 摘要">
          <div className="panel-header">
            <h3>ShaderGraph shadow</h3>
            <span>{graphShadow.status}</span>
          </div>
          <p>
            该结果用于验证 DSL → Compiler → WebGL1 链路，不参与当前产品 GLSL、
            scorer 或 current_best 选择。
          </p>
          <div className="score-grid">
            <div>
              <span>图层 / Primitive</span>
              <strong>
                {formatCount(graphShadow.layer_count)} /{" "}
                {formatCount(graphShadow.primitive_count)}
              </strong>
            </div>
            <div>
              <span>编译 / 缓存命中</span>
              <strong>
                {formatCount(graphShadow.compile_count)} /{" "}
                {formatCount(graphShadow.cache_hit_count)}
              </strong>
            </div>
            <div>
              <span>shadow 渲染耗时</span>
              <strong>{formatMs(graphShadow.render_duration_ms)}</strong>
            </div>
            <div>
              <span>Document hash</span>
              <strong>{shortHash(graphShadow.document_sha256)}</strong>
            </div>
            <div>
              <span>Topology hash</span>
              <strong>{shortHash(graphShadow.topology_sha256)}</strong>
            </div>
            <div>
              <span>Compiler</span>
              <strong>{graphShadow.compiler_version || "—"}</strong>
            </div>
          </div>
          {graphShadow.unsupported_features?.length ? (
            <p>未映射能力：{graphShadow.unsupported_features.join("、")}</p>
          ) : null}
          {graphShadow.error_code ? <p>shadow 错误：{graphShadow.error_code}</p> : null}
          {graphLayers.length ? (
            <details className="scene-mvp-details">
              <summary>图层检查器（{graphLayers.length}）</summary>
              <ol>
                {graphLayers.map((layer, index) => (
                  <li key={String(layer.id || index)}>
                    <strong>{String(layer.id || `layer-${index + 1}`)}</strong>
                    <span>
                      {String(
                        (layer.shape as Record<string, unknown> | undefined)?.kind ||
                          "unknown",
                      )}
                    </span>
                    <span>
                      {String(
                        (layer.fill as Record<string, unknown> | undefined)?.kind ||
                          "unknown",
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
        </section>
      ) : null}
      {trace.length ? (
        <details className="scene-mvp-details">
          <summary>阶段追踪（{trace.length}）</summary>
          <ul className="scene-mvp-trace">
            {trace.map((phase, index) => (
              <li key={`${index}-${phase.phase}`}>
                <strong>{phase.phase}</strong>
                <span className={`trace-status is-${phase.status}`}>{phase.status}</span>
                {typeof phase.duration_ms === "number" ? (
                  <span>{Math.round(phase.duration_ms)} ms</span>
                ) : null}
                {phase.message ? <span>{phase.message}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="scene-mvp-details">
        <summary>{productGraph ? "ShaderGraph JSON" : "场景 JSON"}</summary>
        <pre>{formatSceneJson(minPipeline?.scene)}</pre>
      </details>
    </section>
  );
}
