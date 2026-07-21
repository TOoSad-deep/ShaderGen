"""Effect Genome v0 的稳定拓扑投影与四类 hash。."""

from __future__ import annotations

import heapq
from typing import Any

from shaderforge.contracts import canonical_sha256
from shaderforge.genome.models import EffectGenome, EffectNode, GenomeHashes


def _node_key(node: EffectNode) -> tuple[str, str, int]:
    return (node.semantic_role, node.kind, node.sibling_ordinal)


def _stable_topological_order(genome: EffectGenome) -> tuple[EffectNode, ...]:
    node_by_id = {node.node_id: node for node in genome.nodes}
    indegree = {node_id: 0 for node_id in node_by_id}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    for edge in genome.edges:
        indegree[edge.target_node_id] += 1
        adjacency[edge.source_node_id].append(edge.target_node_id)
    ready = [
        (_node_key(node), node.node_id)
        for node in genome.nodes
        if indegree[node.node_id] == 0
    ]
    heapq.heapify(ready)
    ordered: list[EffectNode] = []
    while ready:
        _, node_id = heapq.heappop(ready)
        ordered.append(node_by_id[node_id])
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                target = node_by_id[target_id]
                heapq.heappush(ready, (_node_key(target), target_id))
    if len(ordered) != len(genome.nodes):
        raise ValueError("Genome 必须是 DAG。")
    return tuple(ordered)


def topology_projection(genome: EffectGenome) -> dict[str, Any]:
    """投影 typed topology，彻底排除参数值和 record id。."""
    ordered = _stable_topological_order(genome)
    canonical_ids = {
        node.node_id: f"node_{index:04d}" for index, node in enumerate(ordered)
    }
    nodes: list[dict[str, Any]] = []
    for node in ordered:
        projected_node: dict[str, Any] = {
            "canonical_node_id": canonical_ids[node.node_id],
            "kind": node.kind,
            "node_version": node.node_version,
            "semantic_role": node.semantic_role,
            "sibling_ordinal": node.sibling_ordinal,
            "inputs": node.inputs,
            "outputs": node.outputs,
            "parameter_bindings": sorted(
                (
                    {
                        "binding_name": binding.binding_name,
                        "parameter_path": binding.parameter_path,
                    }
                    for binding in node.parameter_bindings
                ),
                key=lambda item: (item["binding_name"], item["parameter_path"]),
            ),
        }
        typed_payload = node.model_dump(
            mode="python",
            exclude={
                "node_id",
                "kind",
                "node_version",
                "semantic_role",
                "sibling_ordinal",
                "inputs",
                "outputs",
                "parameter_bindings",
            },
        )
        if typed_payload:
            projected_node["typed_payload"] = typed_payload
        nodes.append(projected_node)
    projected_edges: list[dict[str, Any]] = []
    for edge in genome.edges:
        projected_edge = {
                "source_node_id": canonical_ids[edge.source_node_id],
                "source_port": edge.source_port,
                "target_node_id": canonical_ids[edge.target_node_id],
                "target_port": edge.target_port,
        }
        if hasattr(edge, "sdf_to_mask_conversion"):
            projected_edge["sdf_to_mask_conversion"] = getattr(
                edge, "sdf_to_mask_conversion"
            )
        projected_edges.append(projected_edge)
    edges = sorted(
        projected_edges,
        key=lambda item: (
            item["source_node_id"],
            item["source_port"],
            item["target_node_id"],
            item["target_port"],
        ),
    )
    return {
        "schema_version": genome.schema_version,
        "node_registry_version": genome.node_registry_version,
        "mask_semantics": genome.mask_semantics,
        "sdf_semantics": genome.sdf_semantics,
        "antialias_rule": genome.antialias_rule,
        "nodes": nodes,
        "edges": edges,
        "output_node_id": canonical_ids[genome.output_node_id],
    }


def parameter_layout_projection(genome: EffectGenome) -> list[dict[str, Any]]:
    """投影除 value 外的完整 ParameterSpec 语义。."""
    return sorted(
        (
            parameter.model_dump(mode="python", exclude={"value"})
            for parameter in genome.parameters
        ),
        key=lambda item: item["path"],
    )


def compute_topology_hash(genome: EffectGenome) -> str:
    """计算 topology hash。."""
    return canonical_sha256(
        {"hash_version": genome.hash_version, "topology": topology_projection(genome)}
    )


def compute_parameter_layout_hash(genome: EffectGenome) -> str:
    """计算 parameter layout hash。."""
    return canonical_sha256(
        {
            "hash_version": genome.hash_version,
            "parameters": parameter_layout_projection(genome),
        }
    )


def compute_semantic_genome_hash(genome: EffectGenome) -> str:
    """绑定 contract、topology、layout 与所有参数值。."""
    return canonical_sha256(
        {
            "hash_version": genome.hash_version,
            "contract_id": genome.contract_id,
            "topology_hash": compute_topology_hash(genome),
            "parameter_layout_hash": compute_parameter_layout_hash(genome),
            "parameter_values": sorted(
                (
                    {"path": parameter.path, "value": parameter.value}
                    for parameter in genome.parameters
                ),
                key=lambda item: item["path"],
            ),
        }
    )


def compute_genome_record_hash(genome: EffectGenome) -> str:
    """计算含 genome id/provenance、但不含 URI 的完整记录 hash。."""
    return canonical_sha256(
        {
            "hash_version": genome.hash_version,
            "record": genome.model_dump(mode="python"),
        }
    )


def compute_genome_hashes(genome: EffectGenome) -> GenomeHashes:
    """一次返回四类 Genome hash。."""
    return GenomeHashes(
        topology_hash=compute_topology_hash(genome),
        parameter_layout_hash=compute_parameter_layout_hash(genome),
        semantic_genome_hash=compute_semantic_genome_hash(genome),
        record_hash=compute_genome_record_hash(genome),
    )
