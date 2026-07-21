from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.run_v2_2_compiler_benchmark as runner
from shaderforge.compiler import CompilationProduct, CompilerDefectError
from shaderforge.contracts import canonical_sha256
from shaderforge.genome import EffectGenome

JsonObject = dict[str, Any]


def _read_object(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def test_runner_executes_exactly_three_genomes_for_all_51_frozen_intents(
    tmp_path: Path,
) -> None:
    output = tmp_path / "v2-2-compiler-run"

    result = runner.run_v2_2_compiler_benchmark(output)

    assert result.report.ready
    assert len(result.outcomes) == 51
    assert all(item.success for item in result.outcomes)
    assert all(item.genome_count == 3 for item in result.outcomes)
    assert all(len(set(item.semantic_genome_hashes)) == 3 for item in result.outcomes)
    assert all(item.distinct_structural_signatures >= 2 for item in result.outcomes)
    assert (
        sum(item.deterministic_compile_success_count for item in result.outcomes) == 153
    )
    assert sum(item.static_validation_success_count for item in result.outcomes) == 153
    assert {item.split for item in result.outcomes} == {"development", "validation"}

    config = _read_object(output / "config.json")
    config_payload = dict(config)
    config_sha256 = cast(str, config_payload.pop("config_sha256"))
    assert config_sha256 == canonical_sha256(config_payload)
    assert config["execution_mode"] == "fixture/no-model"
    assert config["model_calls_allowed"] is False
    assert config["model_call_budget"] == 0
    assert config["quality_claim"] == "conformance_static_only"
    assert config["webgl_requested"] is False

    report = _read_object(output / "report.json")
    assert report["config_sha256"] == config_sha256
    assert report["input_intent_outcomes_sha256"] == (
        "cbd83ca7cfa9eb818e906b34e40027180f8531eeae9b12e50f79245c7d492918"
    )
    assert cast(JsonObject, report["legal_genomes"]) == {
        "numerator": 153,
        "denominator": 153,
    }
    assert cast(JsonObject, report["deterministic_compiles"])["numerator"] == 153
    assert cast(JsonObject, report["static_validations"])["numerator"] == 153
    assert report["webgl_requested"] is False
    assert report["webgl_compiles_and_draws"] is None

    summary = _read_object(output / "summary.json")
    assert summary["case_count"] == 51
    assert summary["success_count"] == 51
    assert summary["failure_count"] == 0
    assert summary["model_calls"] == 0
    assert summary["webgl_requested"] is False
    assert len(cast(list[JsonObject], summary["case_record_refs"])) == 51
    assert (output / "intent-input" / "summary.json").is_file()


def test_runner_retains_compile_failure_in_the_51_case_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = cast(
        Callable[[EffectGenome], CompilationProduct],
        getattr(runner, "compile_effect_genome"),
    )
    calls = 0

    def flaky_compile(genome: EffectGenome) -> CompilationProduct:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CompilerDefectError("forced_test_failure", ("test",))
        return original_compile(genome)

    monkeypatch.setattr(runner, "compile_effect_genome", flaky_compile)
    output = tmp_path / "v2-2-compiler-failure"

    result = runner.run_v2_2_compiler_benchmark(output)

    assert not result.report.ready
    assert len(result.outcomes) == 51
    failures = [item for item in result.outcomes if not item.success]
    assert len(failures) == 1
    assert failures[0].failure_code == "deterministic_compile_failed"
    assert failures[0].genome_count == 3
    assert failures[0].deterministic_compile_success_count == 0
    assert result.report.cases_passed.numerator == 50
    assert result.report.cases_passed.denominator == 51
    assert result.report.deterministic_compiles.denominator == 153
    assert result.report.deterministic_compiles.numerator == 150

    outcomes = cast(
        list[JsonObject],
        json.loads((output / "outcomes.json").read_text(encoding="utf-8")),
    )
    assert len(outcomes) == 51
    assert (
        sum(item["failure_code"] == "deterministic_compile_failed" for item in outcomes)
        == 1
    )


def test_runner_requires_an_exclusive_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "already-exists"
    output.mkdir()

    with pytest.raises(FileExistsError):
        runner.run_v2_2_compiler_benchmark(output)
