from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.benchmarks.v2_rendered_gate_collector import (
    V2_3RenderedCaseCollectionIdentity,
    collect_v2_3_verified_rendered_case,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.benchmark import V2_3ActualChromiumReplayRunner
from shaderforge.store import ArtifactRefV2


class _UnusedResolver:
    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        raise AssertionError(f"unexpected resolve: {artifact_id}")

    def read_bytes(self, artifact_id: str) -> bytes:
        raise AssertionError(f"unexpected read: {artifact_id}")


def _identity() -> V2_3RenderedCaseCollectionIdentity:
    return V2_3RenderedCaseCollectionIdentity(
        manifest_id="collector-unit-manifest",
        dataset_version="collector-unit-v1",
        manifest_sha256="a" * 64,
        taxonomy_sha256="b" * 64,
        config_sha256="c" * 64,
        threshold_policy_hash="d" * 64,
        input_intent_outcomes_sha256="e" * 64,
        input_compiler_outcomes_sha256="f" * 64,
        split="development",
        case_id="collector-missing-state",
        source_image_sha256="1" * 64,
        expected_hypothesis_count=1,
    )


@pytest.mark.anyio
async def test_missing_confirmed_state_is_sealed_as_non_ready_case(
    tmp_path: Path,
) -> None:
    result = await collect_v2_3_verified_rendered_case(
        state_store=LocalPngToShaderV2StateStore(tmp_path / "states"),
        run_id="missing-confirmed-run",
        resolver=_UnusedResolver(),
        identity=_identity(),
        replay_runner=V2_3ActualChromiumReplayRunner(),
    )

    outcome = result.capability.outcome
    assert result.receipts == ()
    assert not outcome.success
    assert outcome.expected_seed_attempt_count == 3
    assert outcome.seed_attempt_count == 0
    assert outcome.all_candidate_refs == ()
    assert outcome.actual_replay_receipt_hashes == ()
    assert outcome.actual_replay_receipts_root is None
    assert outcome.failure_codes == (
        "strict_collection_failed:load_confirmed_state:V2StateCheckpointNotFoundError",
    )
    assert all(not row.prediction_available for row in outcome.layer_predictions)


@pytest.mark.anyio
async def test_collector_rejects_non_concrete_state_store(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(TypeError, match="concrete Local V2 State Store"):
        await collect_v2_3_verified_rendered_case(
            state_store=object(),  # type: ignore[arg-type]
            run_id="fake-store-run",
            resolver=_UnusedResolver(),
            identity=_identity(),
            replay_runner=V2_3ActualChromiumReplayRunner(),
        )
