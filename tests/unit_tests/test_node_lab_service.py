from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from nodelab.http.factory import load_application
from nodelab.http.main import create_app
from nodelab.http.settings import NodeLabServiceSettings
from nodelab.provider import NodeProviderBuilder
from nodelab.runner import NodeLabApplication


def _install_factory_module(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = ModuleType(module_name)

    def echo_node(state: Mapping[str, object]) -> dict[str, object]:
        return {"echoed": state["value"]}

    def create_application(
        settings: NodeLabServiceSettings,
    ) -> NodeLabApplication:
        provider = (
            NodeProviderBuilder("external_pipeline")
            .add_node(
                echo_node,
                node_id="echo",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": True,
                },
                output_schema={
                    "type": "object",
                    "properties": {"echoed": {"type": "string"}},
                    "required": ["echoed"],
                    "additionalProperties": False,
                },
                example_inputs={"value": "hello"},
            )
            .build()
        )
        return NodeLabApplication.at_root(settings.root, node_provider=provider)

    module.create_application = create_application  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)


def test_standalone_service_runs_without_agent_provider(tmp_path: Path) -> None:
    settings = NodeLabServiceSettings(
        root=tmp_path / "runs",
        batch_root=tmp_path / "batches",
        pipeline_id="empty_pipeline",
    )
    client = TestClient(create_app(settings))

    health = client.get("/api/lab/v1/health")
    created = client.post(
        "/api/lab/v1/runs",
        json={"initial_state": {"seed": 1}},
    )

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "enabled": True,
        "service_mode": "standalone",
        "pipeline_id": "empty_pipeline",
        "node_count": 0,
        "capability_count": 0,
        "suite_count": 0,
        "real_model_enabled": False,
    }
    assert created.status_code == 200
    assert created.json()["pipeline_id"] == "empty_pipeline"
    assert client.get("/api/lab/v1/nodes").json() == []


def test_application_factory_connects_external_pipeline_over_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_name = "node_lab_external_factory_test"
    _install_factory_module(monkeypatch, module_name)
    settings = NodeLabServiceSettings(
        root=tmp_path / "runs",
        batch_root=tmp_path / "batches",
        pipeline_id="ignored_for_factory",
        application_factory=f"{module_name}:create_application",
    )
    application = load_application(settings)
    client = TestClient(create_app(settings, application=application))

    health = client.get("/api/lab/v1/health").json()
    lab_run_id = client.post("/api/lab/v1/runs", json={}).json()["lab_run_id"]
    executed = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "echo",
            "execution_mode": "deterministic",
            "inputs": {"value": "hello"},
        },
    )
    blocked_real = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "echo",
            "execution_mode": "real",
            "allow_model_call": True,
            "inputs": {"value": "hello"},
        },
    )

    assert health["pipeline_id"] == "external_pipeline"
    assert health["node_count"] == 1
    assert executed.status_code == 200
    assert executed.json()["output"] == {"echoed": "hello"}
    assert blocked_real.status_code == 403
    assert blocked_real.json()["detail"]["code"] == "real_model_not_allowed"


def test_service_settings_freeze_independent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NODELAB_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("NODELAB_BATCH_ROOT", str(tmp_path / "batches"))
    monkeypatch.setenv("NODELAB_PIPELINE_ID", "custom_pipeline")
    monkeypatch.setenv("NODELAB_APPLICATION_FACTORY", "example.factory:create")
    monkeypatch.setenv("NODELAB_REAL_MODEL_ENABLED", "true")
    monkeypatch.setenv("NODELAB_CORS_ORIGINS", "https://lab.example")

    settings = NodeLabServiceSettings.from_env(load_environment=False)

    assert settings.root == (tmp_path / "runs").resolve()
    assert settings.batch_root == (tmp_path / "batches").resolve()
    assert settings.pipeline_id == "custom_pipeline"
    assert settings.application_factory == "example.factory:create"
    assert settings.real_model_enabled is True
    assert settings.cors_origins == ("https://lab.example",)


def test_service_settings_reject_client_style_factory_and_wildcard_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="module:callable"):
        NodeLabServiceSettings(application_factory="../../node.py")

    monkeypatch.setenv("NODELAB_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="不允许使用"):
        NodeLabServiceSettings.from_env(load_environment=False)
