"""Safety and cleanup tests for the JSON-only Studio adapter."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from agent.app.graphs import layerplan_glsl_direct_studio as studio
from tests.direct_fakes import reference_png


class _SafeResult:
    def to_safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "direct_glsl_attempt_result_v1",
            "status": "ok",
            "reference_sha256": "a" * 64,
            "current_best": {"spec_sha256": "b" * 64},
        }


class _FakeOwnedRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.close_count = 0
        self.references: list[bytes] = []

    async def run(self, reference_image: bytes, **_kwargs: object) -> _SafeResult:
        self.references.append(reference_image)
        if self.fail:
            raise RuntimeError("owned attempt failed")
        return _SafeResult()

    async def close(self) -> None:
        self.close_count += 1


def _install_runner(
    monkeypatch: pytest.MonkeyPatch,
    runner: _FakeOwnedRunner,
) -> None:
    monkeypatch.setattr(
        studio,
        "current_layered_direct_glsl_implementation_identity",
        lambda: {"identity_sha256": "c" * 64},
    )
    monkeypatch.setattr(
        studio,
        "create_owned_layerplan_glsl_direct_runner",
        lambda _config: runner,
    )


@pytest.mark.anyio
async def test_registered_graph_runs_without_runtime_context_and_returns_safe_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeOwnedRunner()
    _install_runner(monkeypatch, runner)
    reference = reference_png()

    output = await studio.graph.ainvoke(
        {
            "reference_image_base64": base64.b64encode(reference).decode("ascii"),
            "content_type": "image/png",
            "instruction": "match",
        }
    )

    assert runner.references == [reference]
    assert runner.close_count == 1
    assert output["completed_nodes"] == ("run_owned_attempt",)
    serialized = json.dumps(output)
    for forbidden in (
        "fragment_source",
        "rgb_bytes",
        "png_bytes",
        "layer_plan",
        "private_diagnostics",
        "candidates",
        '"config"',
    ):
        assert forbidden not in serialized


@pytest.mark.anyio
async def test_registered_graph_closes_owned_runner_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeOwnedRunner(fail=True)
    _install_runner(monkeypatch, runner)

    with pytest.raises(RuntimeError, match="owned attempt failed"):
        await studio.graph.ainvoke(
            {
                "reference_image_base64": base64.b64encode(reference_png()).decode(
                    "ascii"
                ),
                "content_type": "image/png",
                "instruction": "match",
            }
        )

    assert runner.close_count == 1


def test_langgraph_config_registers_only_the_safe_adapter() -> None:
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "langgraph.json").read_text(encoding="utf-8"))

    assert config["graphs"] == {
        "layerplan_glsl_direct": (
            "./src/agent/app/graphs/layerplan_glsl_direct_studio.py:graph"
        )
    }
    assert config["env"] == ".env"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not base64!",
        "A" * (studio.MAX_REFERENCE_IMAGE_BASE64_CHARS + 1),
    ],
)
def test_studio_input_rejects_invalid_or_oversized_base64(value: str) -> None:
    with pytest.raises(ValueError):
        studio._decode_reference_base64(value)
