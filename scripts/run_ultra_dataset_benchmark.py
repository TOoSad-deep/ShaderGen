"""通过在线产品 API 并行运行独立 Ultra dataset benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/ultra_dataset/manifest.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "output/benchmarks/ultra-dataset"
EXPECTED_SCHEMA = "ultra_dataset_manifest_v1"
EXPECTED_PRESET = "ultra"
ULTRA_MODEL_CALLS_PER_CASE = 40
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 4
DEFAULT_REQUEST_TIMEOUT_SECONDS = 2_520
DEFAULT_SUITE_WALL_SECONDS = 21_600
RUNTIME_POLICY_PATH = ROOT / "backend/app/core/png_to_shader_runtime_policy.v2.yaml"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _multipart_body(
    *,
    image: bytes,
    filename: str,
    project_id: str,
    instruction: str,
) -> tuple[bytes, str]:
    boundary = f"shadergen-{uuid4().hex}"
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )

    add_field("project_id", project_id)
    add_field("generation_mode", "procedural_v1")
    add_field("quality_preset", EXPECTED_PRESET)
    add_field("instruction", instruction)
    parts.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            b"Content-Type: image/png\r\n\r\n",
            image,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return b"".join(parts), boundary


def _get_bytes(url: str, timeout_seconds: int) -> bytes:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
        return response.read()


def _run_case(task: dict[str, Any]) -> dict[str, Any]:
    case_id = str(task["case_id"])
    case_root = Path(str(task["case_root"]))
    image_path = Path(str(task["image_path"]))
    base_url = str(task["base_url"]).rstrip("/")
    timeout_seconds = int(task["request_timeout_seconds"])
    project_id = str(uuid4())
    started_at = time.perf_counter()
    _write_json(
        case_root / "request.json",
        {
            "case_id": case_id,
            "project_id": project_id,
            "quality_preset": EXPECTED_PRESET,
            "image_sha256": str(task["image_sha256"]),
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    body, boundary = _multipart_body(
        image=image_path.read_bytes(),
        filename=image_path.name,
        project_id=project_id,
        instruction=str(task["instruction"]),
    )
    request = Request(
        f"{base_url}/api/shader/generate",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    status_code = 0
    payload: dict[str, Any]
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            status_code = int(response.status)
            payload = json.loads(response.read())
    except HTTPError as exc:
        status_code = int(exc.code)
        payload = json.loads(exc.read())
    except (TimeoutError, URLError, json.JSONDecodeError) as exc:
        payload = {
            "detail": {
                "code": "transport_failure",
                "message": "Ultra benchmark 请求未返回可解析终态。",
                "error_type": type(exc).__name__,
            }
        }

    _write_json(case_root / "response.json", payload)
    success = status_code == 200
    manifest: dict[str, Any] | None = None
    artifact_errors: list[str] = []
    artifact_sha256: dict[str, str] = {}
    if success:
        for response_field, filename in (
            ("manifest_url", "manifest.json"),
            ("metrics_url", "metrics.json"),
            ("final_render_url", "final-render.png"),
        ):
            relative_url = payload.get(response_field)
            if not isinstance(relative_url, str) or not relative_url:
                if response_field != "metrics_url":
                    artifact_errors.append(f"missing:{response_field}")
                continue
            try:
                data = _get_bytes(urljoin(f"{base_url}/", relative_url), 30)
                _write_bytes(case_root / filename, data)
                artifact_sha256[filename] = sha256(data).hexdigest()
                if filename == "manifest.json":
                    manifest = json.loads(data)
            except (HTTPError, TimeoutError, URLError, json.JSONDecodeError) as exc:
                artifact_errors.append(f"{response_field}:{type(exc).__name__}")

    detail = payload.get("detail") if isinstance(payload, dict) else None
    detail = detail if isinstance(detail, dict) else {}
    if (
        isinstance(manifest, dict)
        and manifest.get("runtime_policy_sha256") != task["runtime_policy_sha256"]
    ):
        artifact_errors.append("runtime_policy_sha256:mismatch")
    actual_model_calls = (
        int(manifest.get("model_call_count", 0)) if isinstance(manifest, dict) else None
    )
    result = {
        "case_id": case_id,
        "status": "succeeded" if success and not artifact_errors else "failed",
        "status_code": status_code,
        "project_id": project_id,
        "run_id": payload.get("run_id") or detail.get("run_id"),
        "stop_reason": payload.get("stop_reason") or detail.get("stop_reason"),
        "best_candidate_id": payload.get("best_candidate_id"),
        "total_loss": (
            payload.get("score", {}).get("total_loss")
            if isinstance(payload.get("score"), dict)
            else None
        ),
        "threshold_passed": (
            isinstance(payload.get("score"), dict)
            and isinstance(payload["score"].get("total_loss"), (int, float))
            and float(payload["score"]["total_loss"]) <= 0.12
        ),
        "model_call_count": actual_model_calls,
        "candidate_count": (
            manifest.get("candidate_count") if isinstance(manifest, dict) else None
        ),
        "compile_repair_count": (
            manifest.get("compile_repair_count") if isinstance(manifest, dict) else None
        ),
        "visual_refinement_count": (
            manifest.get("visual_refinement_count")
            if isinstance(manifest, dict)
            else None
        ),
        "runtime_policy_sha256": (
            manifest.get("runtime_policy_sha256")
            if isinstance(manifest, dict)
            else None
        ),
        "charged_model_calls": (
            actual_model_calls
            if actual_model_calls is not None
            else ULTRA_MODEL_CALLS_PER_CASE
        ),
        "duration_seconds": round(time.perf_counter() - started_at, 3),
        "artifact_errors": artifact_errors,
        "artifact_sha256": artifact_sha256,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    _write_json(case_root / "result.json", result)
    return result


def _load_cases(manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Ultra dataset manifest 必须是 object。")
    if document.get("schema_version") != EXPECTED_SCHEMA:
        raise ValueError("Ultra dataset manifest schema_version 不匹配。")
    if document.get("quality_preset") != EXPECTED_PRESET:
        raise ValueError("Ultra dataset manifest 必须冻结 quality_preset=ultra。")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Ultra dataset manifest cases 不能为空。")
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError("Ultra dataset case 必须是 object。")
        case_id = str(raw.get("case_id", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError("Ultra dataset case_id 不能为空或重复。")
        seen.add(case_id)
        image_path = (manifest_path.parent / str(raw.get("image", ""))).resolve()
        if image_path.suffix.lower() != ".png" or not image_path.is_file():
            raise ValueError(f"Ultra dataset 图片不存在或不是 PNG：{case_id}。")
        image = image_path.read_bytes()
        cases.append(
            {
                "case_id": case_id,
                "image_path": str(image_path),
                "image_bytes": len(image),
                "image_sha256": sha256(image).hexdigest(),
            }
        )
    return document, cases


def _summary(results: list[dict[str, Any]], expected_cases: int) -> dict[str, Any]:
    succeeded = [item for item in results if item["status"] == "succeeded"]
    losses = [
        float(item["total_loss"])
        for item in succeeded
        if item["total_loss"] is not None
    ]
    return {
        "schema_version": "ultra_dataset_report_v1",
        "status": "completed" if len(results) == expected_cases else "incomplete",
        "expected_cases": expected_cases,
        "completed_cases": len(results),
        "succeeded_cases": len(succeeded),
        "failed_cases": len(results) - len(succeeded),
        "threshold_met_cases": sum(1 for item in results if item["threshold_passed"]),
        "mean_total_loss": round(sum(losses) / len(losses), 8) if losses else None,
        "charged_model_calls": sum(
            int(item["charged_model_calls"]) for item in results
        ),
        "duration_seconds_sum": round(
            sum(float(item["duration_seconds"]) for item in results), 3
        ),
        "cases": sorted(results, key=lambda item: str(item["case_id"])),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--model-call-budget", type=int, default=640)
    parser.add_argument(
        "--suite-wall-seconds", type=int, default=DEFAULT_SUITE_WALL_SECONDS
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    parser.add_argument("--instruction", default="")
    parser.add_argument("--cases", help="逗号分隔的 case id；默认运行 manifest 全部。")
    parser.add_argument("--allow-model-calls", action="store_true")
    return parser.parse_args()


def main() -> int:
    """校验硬预算后多进程执行 Ultra dataset，并原子写入逐例证据."""
    args = _parse_args()
    if not args.allow_model_calls:
        raise ValueError("真实 Ultra benchmark 必须显式提供 --allow-model-calls。")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency 必须位于 1..{MAX_CONCURRENCY}。")
    manifest_path = args.manifest.resolve()
    document, cases = _load_cases(manifest_path)
    if args.cases:
        requested = tuple(
            item.strip() for item in args.cases.split(",") if item.strip()
        )
        available = {str(item["case_id"]): item for item in cases}
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise ValueError(f"未知 Ultra dataset case：{', '.join(unknown)}。")
        cases = [available[case_id] for case_id in requested]
    runtime_policy_sha256 = sha256(RUNTIME_POLICY_PATH.read_bytes()).hexdigest()
    reserved_calls = len(cases) * ULTRA_MODEL_CALLS_PER_CASE
    if reserved_calls > args.model_call_budget:
        raise ValueError(
            f"整套预留 {reserved_calls} 次模型调用，超过硬预算 "
            f"{args.model_call_budget}。"
        )
    minimum_suite_wall = (
        (len(cases) + args.concurrency - 1)
        // args.concurrency
        * args.request_timeout_seconds
    )
    if minimum_suite_wall > args.suite_wall_seconds:
        raise ValueError("suite wall-time 不足以覆盖当前 case、并发和单请求硬超时。")

    run_id = "ultra-dataset-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / run_id).resolve()
    if output_dir.exists():
        raise ValueError(f"输出目录已存在，禁止覆盖：{output_dir}。")
    output_dir.mkdir(parents=True)
    expanded_manifest = {
        **document,
        "source_manifest": str(manifest_path.relative_to(ROOT)),
        "source_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
        "cases": cases,
    }
    _write_json(output_dir / "manifest.json", expanded_manifest)
    _write_json(
        output_dir / "config.json",
        {
            "schema_version": "ultra_dataset_run_config_v1",
            "run_id": run_id,
            "base_url": args.base_url,
            "quality_preset": EXPECTED_PRESET,
            "concurrency": args.concurrency,
            "model_call_budget": args.model_call_budget,
            "reserved_model_calls": reserved_calls,
            "suite_wall_seconds": args.suite_wall_seconds,
            "request_timeout_seconds": args.request_timeout_seconds,
            "instruction": args.instruction,
            "selected_case_ids": [case["case_id"] for case in cases],
            "runtime_policy_sha256": runtime_policy_sha256,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )

    tasks = [
        {
            **case,
            "case_root": str(output_dir / "cases" / case["case_id"]),
            "base_url": args.base_url,
            "request_timeout_seconds": args.request_timeout_seconds,
            "instruction": args.instruction,
            "runtime_policy_sha256": runtime_policy_sha256,
        }
        for case in cases
    ]
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(_run_case, task): task for task in tasks}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            report = _summary(results, len(cases))
            report["suite_elapsed_seconds"] = round(time.monotonic() - started, 3)
            _write_json(output_dir / "report.json", report)
            sys.stdout.write(
                json.dumps(
                    {
                        "case_id": result["case_id"],
                        "status": result["status"],
                        "completed": len(results),
                        "total": len(cases),
                        "output_dir": str(output_dir),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            sys.stdout.flush()

    report = _summary(results, len(cases))
    report["suite_elapsed_seconds"] = round(time.monotonic() - started, 3)
    _write_json(output_dir / "report.json", report)
    sys.stdout.write(
        json.dumps(
            {
                "run_id": run_id,
                "status": report["status"],
                "succeeded": report["succeeded_cases"],
                "failed": report["failed_cases"],
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0 if report["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
