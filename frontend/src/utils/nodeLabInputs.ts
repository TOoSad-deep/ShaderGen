/**
 * Node Lab 节点输入与 Artifact 的纯工具函数。
 *
 * 这些函数只依赖 descriptor / example 中的 `artifact_inputs` 映射（字段名 → Artifact kind）
 * 和当前 LabRun 的 Artifact 列表，不硬编码任何具体 Node 或字段。
 */

export interface ArtifactLike {
  artifact_id: string;
  kind: string;
  sha256: string;
  size_bytes?: number;
  created_at?: string;
}

export interface ArtifactEquivalenceContext {
  /** 当前选中的父 Step；若存在，优先使用其 State 中引用的 Artifact ID。 */
  baseStep?: {
    output?: Record<string, unknown>;
    state_diff?: {
      added?: Record<string, unknown>;
      changed?: Record<string, unknown>;
    };
    input_summary?: Record<string, unknown>;
  } | null;
}

export interface ArtifactFillResult {
  /** 填充后的输入对象（浅拷贝）。 */
  inputs: Record<string, unknown>;
  /** 已被自动填充的字段名。 */
  filledFields: string[];
  /** 因存在多个无法证明等价的 Artifact 而未自动填充的字段名。 */
  ambiguousFields: string[];
}

export interface ArtifactInputExample {
  inputs: Record<string, unknown>;
  artifact_inputs: Record<string, string>;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

const PLACEHOLDER_RE = /^replace-with-.*-artifact-id$/i;

/** 判断某个字段值是否仍可被 Artifact ID 自动填充。 */
export function isArtifactPlaceholder(value: unknown): boolean {
  if (value === undefined || value === "") return true;
  if (typeof value === "string") return PLACEHOLDER_RE.test(value);
  return false;
}

/** 获取指定 kind 的 Artifact 候选。 */
export function getArtifactCandidates(
  kind: string,
  artifacts: readonly ArtifactLike[],
): ArtifactLike[] {
  return artifacts.filter((artifact) => artifact.kind === kind);
}

/** 按 sha256 把候选分组。 */
function groupCandidatesBySha256(
  candidates: readonly ArtifactLike[],
): ArtifactLike[][] {
  const groups = new Map<string, ArtifactLike[]>();
  for (const artifact of candidates) {
    const list = groups.get(artifact.sha256) ?? [];
    list.push(artifact);
    groups.set(artifact.sha256, list);
  }
  return Array.from(groups.values());
}

/** 在等价副本组内挑选一个确定的 Artifact ID。
 *
 * 优先匹配父 Step State 中已引用的字段值（Output、State Diff added/changed、输入摘要）；
 * 无匹配时回退到创建最早的 Artifact，保证选择结果稳定且可预测。
 */
function pickArtifactFromEquivalenceGroup(
  group: readonly ArtifactLike[],
  field: string,
  context?: ArtifactEquivalenceContext,
): ArtifactLike | undefined {
  if (context?.baseStep) {
    const parentValue =
      context.baseStep.output?.[field] ??
      context.baseStep.state_diff?.added?.[field] ??
      context.baseStep.state_diff?.changed?.[field] ??
      context.baseStep.input_summary?.[field];
    if (typeof parentValue === "string") {
      const matched = group.find((artifact) => artifact.artifact_id === parentValue);
      if (matched) return matched;
    }
  }
  return [...group].sort((left, right) => {
    const leftTime = left.created_at ? Date.parse(left.created_at) : NaN;
    const rightTime = right.created_at ? Date.parse(right.created_at) : NaN;
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) {
      return leftTime - rightTime;
    }
    return 0;
  })[0];
}

/**
 * 对仍为空或占位符的 artifact 输入字段执行自动填充。
 *
 * - 同一 kind 存在多个 Artifact 时，先按 sha256 分组；
 * - 若所有候选内容等价（sha256 相同），合并为一个逻辑候选并自动填充，
 *   优先选择与父 Step State 一致的 ID，无匹配时选择创建最早的 Artifact；
 * - 若存在多个不同 sha256 的真实候选，视为 ambiguous，保留占位符并由用户下拉选择；
 * - 用户已手工写入的非占位值不会被覆盖；
 * - 字段在 inputs 中不存在时视为空，会被填充。
 */
export function fillArtifactInputs(
  inputs: Record<string, unknown>,
  mapping: Record<string, string>,
  artifacts: readonly ArtifactLike[],
  context?: ArtifactEquivalenceContext,
): ArtifactFillResult {
  const next: Record<string, unknown> = { ...inputs };
  const filledFields: string[] = [];
  const ambiguousFields: string[] = [];

  for (const [field, kind] of Object.entries(mapping)) {
    const current = next[field];
    if (!isArtifactPlaceholder(current)) continue;

    const candidates = getArtifactCandidates(kind, artifacts);
    const groups = groupCandidatesBySha256(candidates);

    if (groups.length === 1) {
      const picked = pickArtifactFromEquivalenceGroup(groups[0], field, context);
      if (picked) {
        next[field] = picked.artifact_id;
        filledFields.push(field);
      }
    } else if (groups.length > 1) {
      ambiguousFields.push(field);
    }
  }

  return { inputs: next, filledFields, ambiguousFields };
}

/**
 * 从示例默认值出发，对 artifact 输入字段执行一次性物化。
 *
 * 用于切换 Node/示例或创建/恢复 LabRun 时生成初始输入：
 * - 普通字段使用 example.inputs 中的默认值；
 * - 仍为空/占位符的 artifact 字段在存在唯一内容等价候选时自动填充；
 * - 存在多个不同内容的真实候选时保留示例占位符，不覆盖手写非占位值（但调用方应保证传入的是示例默认值）。
 */
export function buildExampleInputs(
  example: ArtifactInputExample,
  artifacts: readonly ArtifactLike[],
  context?: ArtifactEquivalenceContext,
): ArtifactFillResult {
  return fillArtifactInputs(example.inputs, example.artifact_inputs, artifacts, context);
}

/** 将示例物化为格式化的节点输入 JSON 文本。 */
export function materializeExampleInputs(
  example: ArtifactInputExample,
  artifacts: readonly ArtifactLike[],
  context?: ArtifactEquivalenceContext,
): string {
  return pretty(buildExampleInputs(example, artifacts, context).inputs);
}

/** 在 JSON 文本中更新某个 artifact 字段，返回格式化后的 JSON；解析失败返回 null。 */
export function updateArtifactInputField(
  text: string,
  field: string,
  artifactId: string,
): string | null {
  let parsed: Record<string, unknown>;
  try {
    const value = JSON.parse(text);
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return null;
    }
    parsed = value as Record<string, unknown>;
  } catch {
    return null;
  }
  parsed[field] = artifactId;
  return JSON.stringify(parsed, null, 2);
}

export interface ArtifactSelectionState {
  /** 当前 JSON 中已匹配候选列表的 Artifact ID；未匹配时为 "" 表示“请选择”。 */
  selectedArtifactId: string;
  /** 当前值是否确实在候选列表中。 */
  isKnown: boolean;
}

/** 根据当前字段值和候选列表计算下拉框应展示的状态。 */
export function getArtifactFieldSelection(
  value: unknown,
  candidates: readonly ArtifactLike[],
): ArtifactSelectionState {
  if (typeof value === "string" && candidates.some((a) => a.artifact_id === value)) {
    return { selectedArtifactId: value, isKnown: true };
  }
  return { selectedArtifactId: "", isKnown: false };
}

/** 生成 Artifact 下拉选项的显示文本。 */
export function formatArtifactOption(artifact: ArtifactLike): string {
  const shortId = artifact.artifact_id.slice(0, 8);
  const time = artifact.created_at ? ` · ${artifact.created_at.slice(0, 19)}` : "";
  return `${shortId}${time}`;
}
