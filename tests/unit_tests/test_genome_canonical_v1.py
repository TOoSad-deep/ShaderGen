from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from shaderforge.contracts.canonical import canonical_json_bytes, canonical_sha256
from shaderforge.genome.canonical import compute_genome_hashes
from shaderforge.genome.models import EffectGenome, GenomeHashes
from tests.fixtures.png_to_shader_v2_contracts import make_genome

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads(
    (ROOT / "tests/fixtures/png_to_shader_v2/golden_hashes_v1.json").read_text(
        encoding="utf-8"
    )
)
CURRENT_GOLDEN = json.loads(
    (ROOT / "tests/fixtures/png_to_shader_v2/golden_hashes_v3.json").read_text(
        encoding="utf-8"
    )
)


def test_canonical_json_nfc_binary64_and_negative_zero_golden() -> None:
    assert (
        canonical_json_bytes({"value": -0.0}).decode()
        == GOLDEN["canonical_examples"]["negative_zero"]
    )
    assert (
        canonical_json_bytes({"e\u0301": "e\u0301"}).decode()
        == GOLDEN["canonical_examples"]["unicode"]
    )
    assert canonical_sha256({"x": -0.0}) == canonical_sha256({"x": 0.0})


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="NaN|Infinity"):
        canonical_json_bytes({"value": value})


def test_genome_four_hashes_match_golden_fixture() -> None:
    assert (
        compute_genome_hashes(make_genome()).model_dump(mode="json")
        == CURRENT_GOLDEN["genome_hashes"]
    )


def test_topology_hash_is_independent_of_record_node_ids_and_collection_order() -> None:
    genome = make_genome()
    original = compute_genome_hashes(genome)
    id_map = {
        "geometry-original": "renamed-geometry",
        "fill-original": "renamed-fill",
        "output-original": "renamed-output",
    }
    renamed_nodes = tuple(
        node.model_copy(update={"node_id": id_map[node.node_id]})
        for node in reversed(genome.nodes)
    )
    renamed_edges = tuple(
        edge.model_copy(
            update={
                "source_node_id": id_map[edge.source_node_id],
                "target_node_id": id_map[edge.target_node_id],
            }
        )
        for edge in reversed(genome.edges)
    )
    reordered = genome.model_copy(
        update={
            "nodes": renamed_nodes,
            "edges": renamed_edges,
            "parameters": tuple(reversed(genome.parameters)),
            "output_node_id": id_map[genome.output_node_id],
        }
    )

    assert compute_genome_hashes(reordered) == original.model_copy(
        update={"record_hash": compute_genome_hashes(reordered).record_hash}
    )
    assert compute_genome_hashes(reordered).record_hash != original.record_hash


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("min_value", 0.02),
        ("max_value", 0.6),
        ("optimizable", False),
        ("block", "shape"),
        ("affected_regions", ("subject", "edge")),
        ("semantic_role", "diameter"),
        ("unit", "normalized"),
        ("coordinate_space", "object_uv"),
        ("color_space", "linear_rgb"),
        ("cyclic", True),
        ("quantization", 0.001),
    ),
)
def test_every_parameter_layout_field_changes_layout_and_semantic_hash(
    field_name: str,
    changed_value: object,
) -> None:
    genome = make_genome()
    baseline = compute_genome_hashes(genome)
    radius = genome.parameters[1].model_copy(update={field_name: changed_value})
    changed = genome.model_copy(
        update={"parameters": (genome.parameters[0], radius, genome.parameters[2])}
    )
    hashes = compute_genome_hashes(changed)

    assert hashes.topology_hash == baseline.topology_hash
    assert hashes.parameter_layout_hash != baseline.parameter_layout_hash
    assert hashes.semantic_genome_hash != baseline.semantic_genome_hash


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("mask_semantics", "changed-mask-semantics"),
        ("sdf_semantics", "changed-sdf-semantics"),
        ("antialias_rule", "changed-antialias-rule"),
        ("node_registry_version", "changed-registry"),
    ),
)
def test_every_top_level_compile_semantic_changes_topology_hash(
    field_name: str,
    changed_value: object,
) -> None:
    genome = make_genome()
    baseline = compute_genome_hashes(genome)
    changed = genome.model_copy(update={field_name: changed_value})
    hashes = compute_genome_hashes(changed)

    assert hashes.topology_hash != baseline.topology_hash
    assert hashes.semantic_genome_hash != baseline.semantic_genome_hash


def test_value_contract_and_provenance_hit_the_expected_hash_layers() -> None:
    genome = make_genome()
    baseline = compute_genome_hashes(genome)
    radius = genome.parameters[1].model_copy(update={"value": 0.31})
    value_changed = genome.model_copy(
        update={"parameters": (genome.parameters[0], radius, genome.parameters[2])}
    )
    value_hashes = compute_genome_hashes(value_changed)
    assert value_hashes.topology_hash == baseline.topology_hash
    assert value_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert value_hashes.semantic_genome_hash != baseline.semantic_genome_hash

    contract_hashes = compute_genome_hashes(
        genome.model_copy(update={"contract_id": "another-contract"})
    )
    assert contract_hashes.topology_hash == baseline.topology_hash
    assert contract_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert contract_hashes.semantic_genome_hash != baseline.semantic_genome_hash

    provenance_hashes = compute_genome_hashes(
        genome.model_copy(
            update={
                "provenance": genome.provenance.model_copy(update={"random_seed": 8})
            }
        )
    )
    assert provenance_hashes.topology_hash == baseline.topology_hash
    assert provenance_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert provenance_hashes.semantic_genome_hash == baseline.semantic_genome_hash
    assert provenance_hashes.record_hash != baseline.record_hash


def test_genome_requires_exactly_one_edge_for_every_input_port() -> None:
    raw = make_genome().model_dump(mode="json")
    raw["edges"].pop()

    with pytest.raises(ValidationError, match="恰好有一条入边"):
        EffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_genome_output_must_be_a_sink() -> None:
    raw = make_genome().model_dump(mode="json")
    extra_output = dict(raw["nodes"][-1])
    extra_output.update(
        node_id="secondary-output",
        semantic_role="secondary_output",
    )
    raw["nodes"].append(extra_output)
    raw["edges"].append(
        {
            "source_node_id": raw["output_node_id"],
            "source_port": "color",
            "target_node_id": "secondary-output",
            "target_port": "color",
        }
    )

    with pytest.raises(ValidationError, match="无出边"):
        EffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_every_genome_node_must_reach_output() -> None:
    raw = make_genome().model_dump(mode="json")
    dead_geometry = dict(raw["nodes"][0])
    dead_geometry.update(
        node_id="dead-geometry",
        semantic_role="dead_geometry",
    )
    raw["nodes"].append(dead_geometry)

    with pytest.raises(ValidationError, match="可达 output_node_id"):
        EffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_genome_hash_fields_are_sha256_hex() -> None:
    with pytest.raises(ValidationError, match="topology_hash"):
        GenomeHashes(
            topology_hash="not-a-hash",
            parameter_layout_hash="1" * 64,
            semantic_genome_hash="2" * 64,
            record_hash="3" * 64,
        )
