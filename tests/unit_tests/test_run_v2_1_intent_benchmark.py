from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.run_v2_1_intent_benchmark as runner
from shaderforge.analysis import (
    TargetMeasurementsV2ArtifactBundle,
    measure_target_v2,
)
from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2DatasetGateStage,
    V2DatasetStageGate,
    evaluate_v2_dataset_stage_gate,
)
from shaderforge.contracts import canonical_sha256
from shaderforge.store import ArtifactCatalog

JsonObject = dict[str, Any]


def _read_object(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def _artifact_entries(output: Path) -> tuple[Path, dict[str, JsonObject]]:
    run_root = output / "artifact-store" / runner.PROJECT_ID / runner.RUN_ID
    manifest = _read_object(run_root / ".artifact-catalog-v2/manifest.json")
    return run_root, cast(dict[str, JsonObject], manifest["artifacts"])


def _artifact_json(
    run_root: Path,
    entries: dict[str, JsonObject],
    artifact_ref: JsonObject,
) -> JsonObject:
    entry = entries[cast(str, artifact_ref["artifact_id"])]
    return _read_object(run_root / cast(str, entry["relative_path"]))


def test_runner_retains_failures_and_materializes_real_predictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def flaky_measure(
        source_bytes: bytes,
        *,
        catalog: ArtifactCatalog,
        run_id: str,
    ) -> TargetMeasurementsV2ArtifactBundle:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("forced measurement failure")
        return measure_target_v2(source_bytes, catalog=catalog, run_id=run_id)

    monkeypatch.setattr(runner, "measure_target_v2", flaky_measure)
    output = tmp_path / "v2-1-intent-run"

    assert runner.main(["--output", str(output)]) == 2

    cli_result = cast(JsonObject, json.loads(capsys.readouterr().out))
    assert cli_result["execution_mode"] == "fixture/no-model"
    assert cli_result["model_calls"] == 0
    assert cli_result["ready"] is False

    config = _read_object(output / "config.json")
    config_payload = dict(config)
    config_sha256 = cast(str, config_payload.pop("config_sha256"))
    assert config_sha256 == canonical_sha256(config_payload)
    assert config["execution_mode"] == "fixture/no-model"
    assert config["model_calls_allowed"] is False
    assert config["model_call_budget"] == 0
    assert config["quality_claim"] == "conformance_only_not_vlm_quality"
    assert config["gate_stage"] == "v2_1_intent"

    summary = _read_object(output / "summary.json")
    assert summary["case_count"] == 51
    assert cast(int, summary["success_count"]) > 0
    assert cast(int, summary["failure_count"]) > 0
    assert summary["model_calls"] == 0
    assert summary["model_provider"] is None
    assert summary["ready"] is False

    outcomes = cast(
        list[JsonObject],
        json.loads((output / "outcomes.json").read_text(encoding="utf-8")),
    )
    assert len(outcomes) == 51
    assert {item["split"] for item in outcomes} == {
        "development",
        "validation",
    }
    assert sum(not cast(bool, item["intent_valid"]) for item in outcomes) > 0

    report = _read_object(output / "report.json")
    assert report["ready"] is False
    assert cast(list[object], report["blockers"])
    for metric_name in (
        "current_10_intent_legal",
        "validation_intent_legal",
        "validation_instance_count_exact",
    ):
        metric = cast(JsonObject, report[metric_name])
        assert {"numerator", "denominator", "value", "ci95"} <= metric.keys()
        assert {"lower", "upper"} <= cast(JsonObject, metric["ci95"]).keys()

    run_root, entries = _artifact_entries(output)
    case_entries = [
        entry
        for entry in entries.values()
        if entry["kind"] == "v2_1_intent_case_record"
    ]
    outcome_entries = [
        entry
        for entry in entries.values()
        if entry["kind"] == "v2_1_intent_case_outcome"
    ]
    assert len(case_entries) == 51
    assert len(outcome_entries) == 51

    case_records = [
        _read_object(run_root / cast(str, entry["relative_path"]))
        for entry in case_entries
    ]
    failure_record = next(
        item
        for item in case_records
        if (
            cast(JsonObject, item["refs"])["failure"] is not None
            and _artifact_json(
                run_root,
                entries,
                cast(
                    JsonObject,
                    cast(JsonObject, item["refs"])["failure"],
                ),
            )["failure_code"]
            == "measurements_failed"
        )
    )
    failure_refs = cast(JsonObject, failure_record["refs"])
    assert failure_refs["source"] is not None
    failure = _artifact_json(
        run_root,
        entries,
        cast(JsonObject, failure_refs["failure"]),
    )
    assert failure["execution_mode"] == "fixture/no-model"
    assert failure["failure_code"] == "measurements_failed"

    success_record = next(
        item
        for item in case_records
        if cast(JsonObject, item["refs"])["intent"] is not None
    )
    success_refs = cast(JsonObject, success_record["refs"])
    for ref_name in (
        "source",
        "measurements",
        "constraint_set",
        "interpretation",
        "intent_build_result",
        "intent",
        "outcome",
    ):
        assert success_refs[ref_name] is not None

    intent = _artifact_json(
        run_root,
        entries,
        cast(JsonObject, success_refs["intent"]),
    )
    outcome = _artifact_json(
        run_root,
        entries,
        cast(JsonObject, success_refs["outcome"]),
    )
    subject = cast(list[JsonObject], intent["objects"])[0]
    required_layers = [
        layer["role"]
        for layer in cast(list[JsonObject], intent["layers"])
        if layer["required"] is True
    ]
    assert outcome["predicted_topology"] == subject["topology"]
    assert outcome["predicted_instance_count"] == subject["instance_count"]
    assert outcome["predicted_required_layers"] == required_layers

    interpretation = _artifact_json(
        run_root,
        entries,
        cast(JsonObject, success_refs["interpretation"]),
    )
    assert "fixture/no-model" in cast(str, interpretation["summary"])
    assert (
        "模型视觉判断"
        in cast(list[JsonObject], interpretation["uncertainties"])[0]["description"]
    )


def test_runner_requires_an_exclusive_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "already-exists"
    output.mkdir()

    with pytest.raises(FileExistsError):
        runner.run_v2_1_intent_benchmark(output)


def test_runner_fails_closed_if_source_changes_after_stage_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes
    changed_path: Path | None = None
    gate_complete = False

    def evaluate_then_change(
        dataset: LoadedV2Dataset,
        *,
        stage: V2DatasetGateStage,
    ) -> V2DatasetStageGate:
        nonlocal changed_path, gate_complete
        report = evaluate_v2_dataset_stage_gate(dataset, stage=stage)
        changed_path = dataset.resolve_image(
            dataset.manifest.split("development").samples[0]
        )
        gate_complete = True
        return report

    def changed_read_bytes(path: Path) -> bytes:
        if gate_complete and path == changed_path:
            return b"changed-after-stage-gate"
        return original_read_bytes(path)

    monkeypatch.setattr(
        runner,
        "evaluate_v2_dataset_stage_gate",
        evaluate_then_change,
    )
    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)
    output = tmp_path / "changed-source-run"

    with pytest.raises(ValueError, match="stage gate 后发生变化"):
        runner.run_v2_1_intent_benchmark(output)

    assert list(output.iterdir()) == []
