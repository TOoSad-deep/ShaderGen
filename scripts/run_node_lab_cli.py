"""直接调用 Node Lab Application API 的逐节点 JSON CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.app.services.node_lab import (
    LabRunCreateRequest,
    NodeLabApplication,
    NodeLabError,
    StepExecutionRequest,
    create_default_model_node_lab_application,
    create_lab_run,
    describe_nodes,
    execute_step,
    get_step,
    list_artifacts,
    list_steps,
    read_artifact,
    upload_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB_ROOT = ROOT / "output/node-lab/cli"
MAX_JSON_ARGUMENT_CHARS = 1_000_000


def _json_object(value: str, *, field: str) -> dict[str, Any]:
    """解析内联 JSON 或 ``@file.json``，并限制输入大小和顶层类型."""
    if value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
    else:
        text = value
    if len(text) > MAX_JSON_ARGUMENT_CHARS:
        raise ValueError(f"{field} JSON 超过字符上限。")
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{field} 必须是 JSON object。")
    return parsed


def _real_model_enabled() -> bool:
    """只把显式 true 视为允许构造默认真实 Gateway."""
    return os.getenv("SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _application(root: Path) -> NodeLabApplication:
    """通过公共组合根创建可跨 CLI 调用恢复的 Application."""
    return create_default_model_node_lab_application(
        root=root,
        real_model_enabled=_real_model_enabled(),
    )


def _add_lab_run_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("lab_run_id")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_LAB_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    nodes = subparsers.add_parser("nodes", help="列出节点 descriptor 或读取单个节点")
    nodes.add_argument("--node-id")

    create = subparsers.add_parser("create-run", help="创建独立 LabRun")
    create.add_argument("--project-id")
    create.add_argument("--initial-state", default="{}", metavar="JSON|@FILE")

    upload = subparsers.add_parser("upload", help="上传私有 Lab Artifact")
    _add_lab_run_id(upload)
    upload.add_argument("file", type=Path)
    upload.add_argument("--kind", required=True)
    upload.add_argument("--content-type")

    execute = subparsers.add_parser("execute-step", help="执行一个 allowlist 节点")
    _add_lab_run_id(execute)
    execute.add_argument("node_id")
    execute.add_argument(
        "--execution-mode",
        choices=("deterministic", "fixture", "mock", "real"),
        required=True,
    )
    execute.add_argument(
        "--effect-mode",
        choices=("preview", "lab_commit", "project_commit"),
        default="lab_commit",
    )
    execute.add_argument("--base-step-id")
    execute.add_argument("--fixture-id")
    execute.add_argument("--mock-response-artifact-id")
    execute.add_argument("--inputs", default="{}", metavar="JSON|@FILE")
    execute.add_argument("--preview-only", action="store_true")
    execute.add_argument("--allow-model-call", action="store_true")

    get = subparsers.add_parser("get-step", help="读取完整步骤响应")
    _add_lab_run_id(get)
    get.add_argument("step_id")

    steps = subparsers.add_parser("list-steps", help="列出步骤 id 和 DAG 摘要")
    _add_lab_run_id(steps)

    artifacts = subparsers.add_parser(
        "list-artifacts",
        help="列出 Artifact descriptor",
    )
    _add_lab_run_id(artifacts)

    download = subparsers.add_parser("download-artifact", help="下载 Artifact payload")
    _add_lab_run_id(download)
    download.add_argument("artifact_id")
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--force", action="store_true")
    return parser


def _write_json(value: object, *, stream: Any | None = None) -> None:
    target = sys.stdout if stream is None else stream
    target.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _cli_input_error(exc: Exception) -> dict[str, str]:
    """把本地输入异常收敛为稳定、不会回显 payload 或路径的错误."""
    if isinstance(exc, json.JSONDecodeError):
        message = "JSON 无法解析。"
    elif isinstance(exc, ValidationError):
        message = "输入不符合 Node Lab 契约。"
    elif isinstance(exc, OSError):
        message = "本地文件读取或写入失败。"
    else:
        message = str(exc)
    return {
        "code": "cli_input_invalid",
        "message": message,
        "error_type": type(exc).__name__,
    }


async def _run(args: argparse.Namespace) -> int:
    application = _application(args.root)
    if args.command == "nodes":
        descriptors = describe_nodes(args.node_id, application=application)
        _write_json({"nodes": [item.to_dict() for item in descriptors]})
        return 0
    if args.command == "create-run":
        record = create_lab_run(
            LabRunCreateRequest(
                project_id=args.project_id,
                initial_state=_json_object(
                    args.initial_state,
                    field="initial_state",
                ),
            ),
            application=application,
        )
        _write_json(record.to_dict())
        return 0
    if args.command == "upload":
        content_type = args.content_type or mimetypes.guess_type(args.file.name)[0]
        descriptor = upload_artifact(
            lab_run_id=args.lab_run_id,
            kind=args.kind,
            content_type=content_type or "application/octet-stream",
            data=args.file.read_bytes(),
            application=application,
        )
        _write_json(descriptor.to_dict())
        return 0
    if args.command == "execute-step":
        response = await execute_step(
            StepExecutionRequest(
                lab_run_id=args.lab_run_id,
                node_id=args.node_id,
                execution_mode=args.execution_mode,
                effect_mode=args.effect_mode,
                preview_only=args.preview_only,
                allow_model_call=args.allow_model_call,
                base_step_id=args.base_step_id,
                fixture_id=args.fixture_id,
                mock_response_artifact_id=args.mock_response_artifact_id,
                inputs=_json_object(args.inputs, field="inputs"),
            ),
            application=application,
        )
        _write_json(response.to_dict())
        return 0 if response.execution_status == "completed" else 1
    if args.command == "get-step":
        _write_json(
            get_step(
                args.lab_run_id,
                args.step_id,
                application=application,
            ).to_dict()
        )
        return 0
    if args.command == "list-steps":
        steps = list_steps(args.lab_run_id, application=application)
        _write_json(
            {
                "lab_run_id": args.lab_run_id,
                "step_ids": [item.step_id for item in steps],
                "steps": [item.to_dict() for item in steps],
            }
        )
        return 0
    if args.command == "list-artifacts":
        artifacts = list_artifacts(args.lab_run_id, application=application)
        _write_json(
            {
                "lab_run_id": args.lab_run_id,
                "artifacts": [item.to_dict() for item in artifacts],
            }
        )
        return 0
    if args.command == "download-artifact":
        if args.output.exists() and not args.force:
            raise ValueError("输出文件已存在；如需覆盖请显式使用 --force。")
        descriptor, data = read_artifact(
            args.lab_run_id,
            args.artifact_id,
            application=application,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
        _write_json(descriptor.to_dict())
        return 0
    raise ValueError("未知 Node Lab CLI 命令。")


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令，并以稳定 JSON stdout 和退出码运行."""
    try:
        return asyncio.run(_run(_parser().parse_args(argv)))
    except NodeLabError as exc:
        _write_json({"error": exc.to_detail()}, stream=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError, ValidationError, ValueError) as exc:
        _write_json({"error": _cli_input_error(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
