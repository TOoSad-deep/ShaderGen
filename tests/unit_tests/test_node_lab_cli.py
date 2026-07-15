from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.run_node_lab_cli as cli
from agent.app.services.node_lab import create_node_lab_application, get_step

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"


def invoke(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    *arguments: str,
) -> dict[str, Any]:
    exit_code = cli.main(["--root", str(root), *arguments])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert captured.err == ""
    return json.loads(captured.out)


def test_cli_covers_manual_step_artifact_and_dag_flow_with_application_parity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lab_root = tmp_path / "lab"
    catalog = invoke(capsys, lab_root, "nodes")
    assert len(catalog["nodes"]) == 20
    assert "prepare_measurement_seed" in {item["node_id"] for item in catalog["nodes"]}
    assert all(item["input_examples"] for item in catalog["nodes"])

    run = invoke(
        capsys,
        lab_root,
        "create-run",
        "--project-id",
        "project-cli-parity",
        "--initial-state",
        '{"seed":7}',
    )
    lab_run_id = run["lab_run_id"]
    uploaded = invoke(
        capsys,
        lab_root,
        "upload",
        lab_run_id,
        str(REFERENCE_IMAGE),
        "--kind",
        "source_png",
    )
    inputs_path = tmp_path / "initialize-inputs.json"
    inputs_path.write_text(
        json.dumps(
            {
                "source_artifact_id": uploaded["artifact_id"],
                "quality_preset": "balanced",
            }
        ),
        encoding="utf-8",
    )
    executed = invoke(
        capsys,
        lab_root,
        "execute-step",
        lab_run_id,
        "initialize_run",
        "--execution-mode",
        "deterministic",
        "--inputs",
        f"@{inputs_path}",
    )

    fetched = invoke(
        capsys,
        lab_root,
        "get-step",
        lab_run_id,
        executed["step_id"],
    )
    listed_steps = invoke(capsys, lab_root, "list-steps", lab_run_id)
    listed_artifacts = invoke(capsys, lab_root, "list-artifacts", lab_run_id)
    downloaded_path = tmp_path / "downloaded.png"
    downloaded = invoke(
        capsys,
        lab_root,
        "download-artifact",
        lab_run_id,
        uploaded["artifact_id"],
        "--output",
        str(downloaded_path),
    )

    assert fetched == executed
    assert listed_steps["step_ids"] == [executed["step_id"]]
    assert listed_steps["steps"][0]["node_id"] == "initialize_run"
    assert uploaded["artifact_id"] in {
        item["artifact_id"] for item in listed_artifacts["artifacts"]
    }
    assert downloaded == uploaded
    assert downloaded_path.read_bytes() == REFERENCE_IMAGE.read_bytes()

    direct = get_step(
        lab_run_id,
        executed["step_id"],
        application=create_node_lab_application(root=lab_root),
    )
    assert direct.to_dict() == executed


def test_cli_reports_invalid_json_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "--root",
            str(tmp_path / "lab"),
            "create-run",
            "--initial-state",
            "not-json",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    error = json.loads(captured.err)["error"]
    assert error["code"] == "cli_input_invalid"
    assert error["error_type"] == "JSONDecodeError"
    assert error["message"] == "JSON 无法解析。"
    assert "not-json" not in captured.err
    assert "Traceback" not in captured.err
