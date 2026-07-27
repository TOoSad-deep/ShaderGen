/**
 * Node Lab base_step_id 推荐逻辑。
 *
 * 仅依赖示例声明的父节点 node_id 与当前 LabRun 的 Step 列表，
 * 不了解具体生产 Node 的 State 形状。
 */

export interface StepLike {
  step_id: string;
  node_id: string;
  execution_status: "completed" | "failed";
}

/**
 * 根据示例的 `base_step_node_id`，在 steps 中挑选对应 node_id 的最新可用 Step。
 *
 * - 无 `base_step_node_id` 或无匹配时返回 ""（Root State）；
 * - 只考虑 `execution_status === "completed"` 的 Step，忽略失败；
 * - 有多个同 node_id Step 时返回数组中最后一个（即最新）。
 */
export function resolveRecommendedBaseStepId(
  baseStepNodeId: string | null,
  steps: readonly StepLike[],
): string {
  if (!baseStepNodeId) return "";
  const candidates = steps.filter(
    (step) => step.node_id === baseStepNodeId && step.execution_status === "completed",
  );
  return candidates.at(-1)?.step_id ?? "";
}
