import type { NodeLabNodeDescriptor } from "../../api/nodeLab";

interface NodeCatalogProps {
  visibleNodes: NodeLabNodeDescriptor[];
  selectedNodeId: string;
  search: string;
  onSearchChange(value: string): void;
  onSelect(nodeId: string): void;
}

/** 左侧节点目录：搜索与筛选。空 Application / 离线引导由 LabOnboarding 整宽呈现。 */
export function NodeCatalog({
  visibleNodes,
  selectedNodeId,
  search,
  onSearchChange,
  onSelect,
}: NodeCatalogProps) {
  return (
    <aside className="node-lab-catalog" aria-label="节点目录">
      <div className="node-lab-section-heading">
        <h2>节点目录</h2>
        <span>{visibleNodes.length}</span>
      </div>
      <input
        aria-label="搜索节点"
        className="node-lab-search"
        value={search}
        placeholder="搜索 node_id / 分类 / 摘要"
        onChange={(event) => onSearchChange(event.target.value)}
      />
      <div className="node-lab-node-list" aria-label="可用节点">
        {visibleNodes.map((node, index) => (
          <button
            type="button"
            aria-current={node.node_id === selectedNodeId}
            key={node.node_id}
            className={node.node_id === selectedNodeId ? "is-selected" : ""}
            title={node.node_id}
            onClick={() => onSelect(node.node_id)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{node.node_id}</strong>
            <small>
              {node.category}
              {node.requires_model ? " · model" : ""}
            </small>
          </button>
        ))}
        {!visibleNodes.length ? (
          <p className="node-lab-empty-note">没有匹配“{search}”的节点。</p>
        ) : null}
      </div>
    </aside>
  );
}
