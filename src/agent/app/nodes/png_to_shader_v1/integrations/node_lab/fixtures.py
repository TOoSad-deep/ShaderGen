"""PNG-to-Shader V1 的轻量无模型 Fixture Registry."""

from nodelab.fixtures import FixtureDefinition, FixtureRegistry


def build_png_to_shader_v1_fixture_registry() -> FixtureRegistry:
    """构造 V1 路由节点的最小 fixture."""
    return FixtureRegistry(
        [
            FixtureDefinition(
                fixture_id="decide-after-render-success-v1",
                node_id="decide_after_render",
                fixture_version="v1",
                input_state={
                    "render_status": "success",
                    "cancelled": False,
                    "stop_reason": "",
                    "budget_policy": {
                        "max_model_calls": 6,
                        "max_compile_repairs": 1,
                    },
                },
                output_patch={"next_action": "select"},
                expected_outcome="success",
                next_action="select",
                tags=["fixture", "routing", "ai-off"],
            )
        ]
    )


__all__ = ["build_png_to_shader_v1_fixture_registry"]
