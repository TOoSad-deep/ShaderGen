"""比较 Node Lab Application API 与 HTTP transport 的语义和分段延迟."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app.services.node_lab import create_node_lab_application
from nodelab.benchmark import source_environment
from nodelab.models import CapabilityExecutionRequest, LabRunCreateRequest
from nodelab_service.routes import router as node_lab_router
from nodelab_service.service import NodeLabHttpService
from shaderforge.store import RunArtifactStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "benchmarks/png_to_shader_v1/images/solid_circle.png"
DEFAULT_OUTPUT_ROOT = ROOT / "output/benchmarks/node-lab-transport"
DEFAULT_LAB_ROOT = ROOT / "output/node-lab/transport-runs"
TRANSPORT_SUITE_ID = "node_lab_transport_v1"
EXIT_OK = 0
EXIT_CASE_FAILED = 1
EXIT_CONFIGURATION_ERROR = 2
EXIT_INTERNAL_ERROR = 3
EXIT_INTERRUPTED = 130
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DURATION_KEYS = (
    "direct_upload",
    "direct_execute",
    "direct_download",
    "direct_total",
    "http_upload",
    "http_execute",
    "http_download",
    "http_total",
    "transport_overhead",
)


def _write_stdout(value: dict[str, object]) -> None:
    """Stdout 只输出一行稳定机器摘要."""
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--lab-root", type=Path, default=DEFAULT_LAB_ROOT)
    parser.add_argument("--suite-run-id")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--require-passed", action="store_true")
    return parser


def _stable_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    return {
        "p50": round(float(statistics.median(values)), 6),
        "p95": (
            round(
                float(statistics.quantiles(values, n=100, method="inclusive")[94]),
                6,
            )
            if len(values) >= 20
            else None
        ),
        "max": round(max(values), 6),
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 6)


def _safe_run_id(value: str) -> str:
    if not _RUN_ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError("suite_run_id 包含非法字符。")
    return value


def _normalize_semantic_value(value: Any) -> Any:
    """递归移除每次运行必然不同的不透明 id 与时间戳."""
    if isinstance(value, dict):
        return {
            key: _normalize_semantic_value(item)
            for key, item in value.items()
            if key not in {"artifact_id", "lab_run_id", "created_at"}
        }
    if isinstance(value, list):
        return [_normalize_semantic_value(item) for item in value]
    return value


def _semantic_view(response: dict[str, Any]) -> dict[str, Any]:
    """排除 transport/运行 id，只比较领域语义与资源使用."""
    normalized = _normalize_semantic_value(
        {
            "execution_status": response.get("execution_status"),
            "outcome": response.get("outcome"),
            "output": response.get("output"),
            "diagnostics": response.get("diagnostics"),
            "provenance": response.get("provenance"),
            "usage": response.get("usage"),
        }
    )
    if not isinstance(normalized, dict):
        raise TypeError("semantic view 必须是 JSON object。")
    return normalized


def _write_transport_interruption(
    *,
    store: RunArtifactStore,
    run_id: str,
    config_sha256: str,
    attempt_id: str,
    warmup: bool,
    stage: str,
    error_type: str,
    duration_ms: dict[str, float | None],
    evidence: dict[str, Any],
    semantic_view: dict[str, Any] | None,
) -> None:
    """保留 transport 中断现场，恢复时仍重跑同一 attempt."""
    root = store.path_for(f"attempts/{attempt_id}/interruptions")
    existing = list(root.glob("interruption-*.json")) if root.exists() else []
    interruption_id = f"interruption-{len(existing) + 1:03d}"
    store.write_json(
        f"attempts/{attempt_id}/interruptions/{interruption_id}.json",
        {
            "schema_version": "node_lab_transport_interruption_v1",
            "suite_run_id": run_id,
            "config_sha256": config_sha256,
            "attempt_id": attempt_id,
            "interruption_id": interruption_id,
            "warmup": warmup,
            "error_code": "execution_interrupted",
            "error_type": error_type,
            "stage": stage,
            "duration_ms": duration_ms,
            "evidence": evidence,
            "semantic_view": semantic_view,
        },
    )


async def _execute_transport_attempt(
    *,
    application: Any,
    client: TestClient,
    store: RunArtifactStore,
    reference: bytes,
    reference_sha256: str,
    run_id: str,
    config_sha256: str,
    attempt_id: str,
    warmup: bool,
) -> None:
    """执行一次 direct/HTTP 对照；普通失败也原子写入 attempt 证据."""
    duration_ms: dict[str, float | None] = {key: None for key in _DURATION_KEYS}
    application_duration_ms: dict[str, float | None] = {
        "direct": None,
        "http": None,
    }
    evidence: dict[str, Any] = {
        "reference_sha256": reference_sha256,
        "direct_output_sha256": None,
        "http_output_sha256": None,
        "http_header_sha256": None,
    }
    failures: list[str] = []
    direct_response: dict[str, Any] | None = None
    http_response: dict[str, Any] | None = None
    direct_total_started = time.perf_counter()
    http_total_started: float | None = None
    stage = "direct_create_run"
    prefix = f"attempts/{attempt_id}"

    try:
        direct_run = application.create_run(LabRunCreateRequest())
        stage = "direct_upload"
        started = time.perf_counter()
        direct_input = application.upload_artifact(
            lab_run_id=direct_run.lab_run_id,
            kind="reference_png",
            content_type="image/png",
            data=reference,
        )
        duration_ms["direct_upload"] = _elapsed_ms(started)
        stage = "direct_execute"
        started = time.perf_counter()
        direct_response_model = await application.execute_capability(
            CapabilityExecutionRequest(
                lab_run_id=direct_run.lab_run_id,
                capability_id="measure-target",
                inputs={"reference_artifact_id": direct_input.artifact_id},
            )
        )
        duration_ms["direct_execute"] = _elapsed_ms(started)
        direct_response = direct_response_model.to_dict()
        application_duration_ms["direct"] = float(direct_response["duration_ms"])
        direct_output_descriptor = direct_response_model.artifacts[0]
        stage = "direct_download"
        started = time.perf_counter()
        _direct_descriptor, direct_output = application.read_artifact(
            direct_run.lab_run_id,
            direct_output_descriptor.artifact_id,
        )
        duration_ms["direct_download"] = _elapsed_ms(started)
        duration_ms["direct_total"] = _elapsed_ms(direct_total_started)
        direct_sha256 = sha256(direct_output).hexdigest()
        evidence["direct_output_sha256"] = direct_sha256
        store.write_bytes(
            f"{prefix}/direct-output.json",
            direct_output,
            content_type="application/json",
        )

        http_total_started = time.perf_counter()
        stage = "http_create_run"
        http_run_response = client.post("/api/lab/v1/runs", json={})
        http_run_response.raise_for_status()
        http_run_id = str(http_run_response.json()["lab_run_id"])
        stage = "http_upload"
        started = time.perf_counter()
        http_input_response = client.post(
            f"/api/lab/v1/runs/{http_run_id}/artifacts",
            data={"kind": "reference_png"},
            files={"file": ("reference.png", reference, "image/png")},
        )
        duration_ms["http_upload"] = _elapsed_ms(started)
        http_input_response.raise_for_status()
        http_input = http_input_response.json()
        stage = "http_execute"
        started = time.perf_counter()
        http_execution_response = client.post(
            f"/api/lab/v1/runs/{http_run_id}/capabilities/measure-target",
            json={
                "inputs": {
                    "reference_artifact_id": http_input["artifact_id"],
                }
            },
        )
        duration_ms["http_execute"] = _elapsed_ms(started)
        http_execution_response.raise_for_status()
        http_response = http_execution_response.json()
        application_duration_ms["http"] = float(http_response["duration_ms"])
        http_output_descriptor = http_response["artifacts"][0]
        stage = "http_download"
        started = time.perf_counter()
        http_download_response = client.get(
            f"/api/lab/v1/runs/{http_run_id}/artifacts/"
            f"{http_output_descriptor['artifact_id']}"
        )
        duration_ms["http_download"] = _elapsed_ms(started)
        http_download_response.raise_for_status()
        http_output = http_download_response.content
        duration_ms["http_total"] = _elapsed_ms(http_total_started)
        http_sha256 = sha256(http_output).hexdigest()
        evidence["http_output_sha256"] = http_sha256
        evidence["http_header_sha256"] = http_download_response.headers.get(
            "x-artifact-sha256"
        )
        store.write_bytes(
            f"{prefix}/http-output.json",
            http_output,
            content_type="application/json",
        )

        if _semantic_view(direct_response) != _semantic_view(http_response):
            failures.append("semantic_response_mismatch")
        if direct_sha256 != http_sha256:
            failures.append("download_payload_mismatch")
        if direct_sha256 != direct_output_descriptor.sha256:
            failures.append("direct_descriptor_hash_mismatch")
        if http_sha256 != http_output_descriptor["sha256"]:
            failures.append("http_descriptor_hash_mismatch")
        if http_sha256 != evidence["http_header_sha256"]:
            failures.append("http_header_hash_mismatch")
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        if duration_ms["direct_total"] is None:
            duration_ms["direct_total"] = _elapsed_ms(direct_total_started)
        if http_total_started is not None and duration_ms["http_total"] is None:
            duration_ms["http_total"] = _elapsed_ms(http_total_started)
        _write_transport_interruption(
            store=store,
            run_id=run_id,
            config_sha256=config_sha256,
            attempt_id=attempt_id,
            warmup=warmup,
            stage=stage,
            error_type=type(exc).__name__,
            duration_ms=duration_ms,
            evidence=evidence,
            semantic_view=(
                _semantic_view(direct_response) if direct_response is not None else None
            ),
        )
        raise
    except Exception as exc:
        failures.append(f"{stage}:{type(exc).__name__}")
    finally:
        if duration_ms["direct_total"] is None:
            duration_ms["direct_total"] = _elapsed_ms(direct_total_started)
        if http_total_started is not None and duration_ms["http_total"] is None:
            duration_ms["http_total"] = _elapsed_ms(http_total_started)

    direct_total = duration_ms["direct_total"]
    http_total = duration_ms["http_total"]
    if direct_total is not None and http_total is not None:
        duration_ms["transport_overhead"] = round(http_total - direct_total, 6)
    store.write_json(
        f"{prefix}/execution.json",
        {
            "schema_version": "node_lab_transport_attempt_v1",
            "attempt_id": attempt_id,
            "warmup": warmup,
            "correctness_passed": not failures,
            "correctness_failures": failures,
            "duration_ms": duration_ms,
            "application_duration_ms": application_duration_ms,
            "evidence": evidence,
            "semantic_view": (
                _semantic_view(direct_response) if direct_response is not None else None
            ),
        },
    )


async def run_transport_benchmark(
    *,
    reference_path: Path,
    output_root: Path,
    lab_root: Path,
    suite_run_id: str | None,
    repetitions: int,
    warmups: int,
) -> dict[str, Any]:
    """运行 measure-target 的 direct/HTTP 对照并保存可恢复证据."""
    if not 1 <= repetitions <= 100:
        raise ValueError("repetitions 必须在 1 到 100 之间。")
    if not 0 <= warmups <= 20:
        raise ValueError("warmups 必须在 0 到 20 之间。")
    reference = reference_path.resolve().read_bytes()
    reference_sha256 = sha256(reference).hexdigest()
    run_id = _safe_run_id(suite_run_id or f"transport-{uuid4().hex[:12]}")
    store = RunArtifactStore(output_root.resolve() / run_id)
    environment, source_fingerprint, environment_fingerprint = source_environment(
        extra_source_paths=(
            Path(__file__).resolve(),
            ROOT / "src/agent/app/services/node_lab.py",
            ROOT / "src/nodelab_service/main.py",
            ROOT / "src/nodelab_service/routes.py",
            ROOT / "src/nodelab_service/schemas.py",
            ROOT / "src/nodelab_service/service.py",
        )
    )
    config_base = {
        "schema_version": "node_lab_transport_config_v1",
        "suite_run_id": run_id,
        "capability_id": "measure-target",
        "reference_sha256": reference_sha256,
        "repetitions": repetitions,
        "warmups": warmups,
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
    }
    config_sha256 = sha256(_stable_json_bytes(config_base)).hexdigest()
    config = {**config_base, "config_sha256": config_sha256}
    config_path = store.path_for("config.json")
    if config_path.is_file():
        if json.loads(config_path.read_bytes()) != config:
            raise ValueError("suite_run_id 已存在且 config hash 不一致，禁止恢复。")
    else:
        store.write_json("config.json", config)
        store.write_json("environment.json", environment)
        store.write_bytes("inputs/reference.png", reference, content_type="image/png")

    application = create_node_lab_application(root=lab_root)
    app = FastAPI()
    app.state.node_lab_service = NodeLabHttpService(
        application,
        batch_output_root=output_root / "http-batches-disabled",
    )
    app.include_router(node_lab_router)

    with TestClient(app) as client:
        for index in range(warmups + repetitions):
            warmup = index < warmups
            ordinal = index + 1 if warmup else index - warmups + 1
            attempt_id = f"warmup-{ordinal:03d}" if warmup else f"attempt-{ordinal:03d}"
            attempt_path = f"attempts/{attempt_id}/execution.json"
            if store.path_for(attempt_path).is_file():
                continue
            await _execute_transport_attempt(
                application=application,
                client=client,
                store=store,
                reference=reference,
                reference_sha256=reference_sha256,
                run_id=run_id,
                config_sha256=config_sha256,
                attempt_id=attempt_id,
                warmup=warmup,
            )

    measured = [
        json.loads(path.read_bytes())
        for path in sorted(store.path_for("attempts").glob("attempt-*/execution.json"))
    ]
    interruptions = [
        json.loads(path.read_bytes())
        for path in sorted(
            store.path_for("attempts").glob(
                "attempt-*/interruptions/interruption-*.json"
            )
        )
    ]
    failed_completed = [
        str(item["attempt_id"]) for item in measured if not item["correctness_passed"]
    ]
    failed_interrupted = [
        f"{item['attempt_id']}:{item['interruption_id']}" for item in interruptions
    ]
    failed = [*failed_completed, *failed_interrupted]
    passed_count = sum(1 for item in measured if item["correctness_passed"])
    total_count = len(measured) + len(interruptions)
    report = {
        "schema_version": "node_lab_transport_report_v1",
        "suite_id": TRANSPORT_SUITE_ID,
        "suite_run_id": run_id,
        "capability_id": "measure-target",
        "config_sha256": config_sha256,
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "attempt_count": total_count,
        "completed_attempt_count": len(measured),
        "interrupted_attempt_count": len(interruptions),
        "passed_attempt_count": passed_count,
        "failed_attempt_count": len(failed),
        "correctness_rate": passed_count / total_count if total_count else 0.0,
        "failed_attempts": failed,
        "duration_ms": {
            key: _percentiles(
                [
                    float(value)
                    for item in measured
                    if isinstance((value := item["duration_ms"][key]), (int, float))
                ]
            )
            for key in _DURATION_KEYS
        },
    }
    store.write_json("report.json", report)
    return report


async def _run(args: argparse.Namespace) -> int:
    report = await run_transport_benchmark(
        reference_path=args.reference,
        output_root=args.output_root,
        lab_root=args.lab_root,
        suite_run_id=args.suite_run_id,
        repetitions=args.repetitions,
        warmups=args.warmups,
    )
    failed_count = int(report["failed_attempt_count"])
    suite_run_id = str(report["suite_run_id"])
    _write_stdout(
        {
            "suite_id": TRANSPORT_SUITE_ID,
            "suite_run_id": suite_run_id,
            "status": "passed" if failed_count == 0 else "failed",
            "report_path": str(
                Path(args.output_root).resolve() / suite_run_id / "report.json"
            ),
        }
    )
    return EXIT_CASE_FAILED if failed_count else EXIT_OK


def main() -> int:
    """解析参数并运行 transport benchmark."""
    try:
        return asyncio.run(_run(_parser().parse_args()))
    except KeyboardInterrupt:
        sys.stderr.write("node-lab transport benchmark interrupted.\n")
        return EXIT_INTERRUPTED
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"node-lab transport benchmark failed: {exc}\n")
        return EXIT_CONFIGURATION_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI 必须稳定区分内部错误
        sys.stderr.write(
            "node-lab transport benchmark internal error: "
            f"{type(exc).__name__}; evidence preserved when available.\n"
        )
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
