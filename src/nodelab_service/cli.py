"""Node Lab 独立服务命令行入口."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import uvicorn

from nodelab_service.main import create_app
from nodelab_service.settings import NodeLabServiceSettings


def build_parser() -> argparse.ArgumentParser:
    """构造无项目领域参数的启动 CLI."""
    parser = argparse.ArgumentParser(description="运行独立 Node Lab Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--batch-root", type=Path)
    parser.add_argument("--pipeline-id")
    parser.add_argument("--application-factory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析参数并运行独立 Uvicorn 进程."""
    args = build_parser().parse_args(argv)
    settings = NodeLabServiceSettings.from_env()
    settings = replace(
        settings,
        root=args.root or settings.root,
        batch_root=args.batch_root or settings.batch_root,
        pipeline_id=args.pipeline_id or settings.pipeline_id,
        application_factory=(args.application_factory or settings.application_factory),
    )
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_level=settings.log_level.lower(),
    )
    return 0


__all__ = ["build_parser", "main"]
