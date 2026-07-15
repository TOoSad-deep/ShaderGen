from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

import scripts.run_node_lab_benchmark as benchmark_cli
import scripts.run_node_lab_transport_benchmark as transport_cli


class _FakeBenchmarkApplication:
    def __init__(self, report: dict[str, Any]) -> None:
        self._report = report

    def validate_suite(self, manifest: Path) -> dict[str, object]:
        del manifest
        return {
            "suite_id": "node_lab_ai_off_v1",
            "manifest_sha256": "a" * 64,
        }

    async def run_suite(
        self,
        manifest: Path,
        *,
        output_root: Path,
        suite_run_id: str | None,
    ) -> dict[str, Any]:
        del manifest, output_root, suite_run_id
        return self._report


def _benchmark_args(tmp_path: Path, *, validate_only: bool = False) -> Namespace:
    return Namespace(
        compare_baseline=None,
        compare_candidate=None,
        suite_id=None,
        manifest=tmp_path / "manifest.yaml",
        output_root=tmp_path / "output",
        lab_root=tmp_path / "lab",
        suite_run_id="cli-suite",
        validate_only=validate_only,
        require_passed=False,
    )


def test_deterministic_cli_stdout_is_stable_and_failure_is_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = {
        "suite_id": "node_lab_ai_off_v1",
        "suite_run_id": "cli-suite",
        "failed_attempt_count": 1,
    }
    monkeypatch.setattr(
        benchmark_cli,
        "create_node_lab_application",
        lambda *, root: _FakeBenchmarkApplication(report),
    )

    exit_code = asyncio.run(benchmark_cli._run(_benchmark_args(tmp_path)))

    assert exit_code == benchmark_cli.EXIT_CASE_FAILED
    assert json.loads(capsys.readouterr().out) == {
        "suite_id": "node_lab_ai_off_v1",
        "suite_run_id": "cli-suite",
        "status": "failed",
        "report_path": str((tmp_path / "output/cli-suite/report.json").resolve()),
    }


def test_deterministic_cli_validate_only_uses_compact_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "create_node_lab_application",
        lambda *, root: _FakeBenchmarkApplication({}),
    )

    exit_code = asyncio.run(
        benchmark_cli._run(_benchmark_args(tmp_path, validate_only=True))
    )

    assert exit_code == benchmark_cli.EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "suite_id": "node_lab_ai_off_v1",
        "status": "valid",
        "manifest_sha256": "a" * 64,
    }


def test_transport_cli_stdout_is_stable_and_failure_is_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "suite_id": transport_cli.TRANSPORT_SUITE_ID,
            "suite_run_id": "transport-cli-suite",
            "failed_attempt_count": 2,
        }

    monkeypatch.setattr(transport_cli, "run_transport_benchmark", fake_run)
    args = Namespace(
        reference=tmp_path / "reference.png",
        output_root=tmp_path / "output",
        lab_root=tmp_path / "lab",
        suite_run_id="transport-cli-suite",
        repetitions=1,
        warmups=0,
        require_passed=False,
    )

    exit_code = asyncio.run(transport_cli._run(args))

    assert exit_code == transport_cli.EXIT_CASE_FAILED
    assert json.loads(capsys.readouterr().out) == {
        "suite_id": transport_cli.TRANSPORT_SUITE_ID,
        "suite_run_id": "transport-cli-suite",
        "status": "failed",
        "report_path": str(
            (tmp_path / "output/transport-cli-suite/report.json").resolve()
        ),
    }
