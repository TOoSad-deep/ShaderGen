"""Node Lab 内置 AI-off suite 的显式 allowlist."""

from __future__ import annotations

from pathlib import Path

from agent.app.lab.models import NodeLabError

ROOT = Path(__file__).resolve().parents[4]
SUITE_ROOT = ROOT / "benchmarks/node_lab/png_to_shader_v1"

_SUITES = {
    "node_lab_ai_off_v1": SUITE_ROOT / "manifest.yaml",
    "node_lab_scenario_ai_off_v1": SUITE_ROOT / "scenario-manifest.yaml",
    "node_lab_renderer_warm_ai_off_v1": SUITE_ROOT / "renderer-warm-manifest.yaml",
}


def describe_registered_suites() -> tuple[str, ...]:
    """返回稳定 suite id，不暴露本地路径."""
    return tuple(_SUITES)


def resolve_registered_suite(suite_id: str) -> Path:
    """只解析仓库内已登记 manifest，未知 id fail closed."""
    try:
        path = _SUITES[suite_id]
    except KeyError as exc:
        raise NodeLabError(
            "suite_not_found",
            "Node Lab suite 不在 allowlist 中。",
            stage="suite_registry",
            details={"suite_id": suite_id},
        ) from exc
    if not path.is_file():
        raise NodeLabError(
            "suite_not_found",
            "Node Lab suite manifest 不存在。",
            stage="suite_registry",
            details={"suite_id": suite_id},
        )
    return path


__all__ = ["describe_registered_suites", "resolve_registered_suite"]
