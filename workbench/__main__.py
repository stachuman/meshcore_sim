"""__main__.py — CLI entry point: python3 -m workbench [topology] [--trace FILE]."""

from __future__ import annotations

import argparse
import sys

from nicegui import ui

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m workbench",
        description="MeshCore simulation workbench (NiceGUI)",
    )
    parser.add_argument(
        "topology",
        nargs="?",
        default=None,
        help="Path to topology JSON file",
    )
    parser.add_argument(
        "--trace",
        default=None,
        help="Path to trace JSON file to load in Trace Viewer",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port to serve on (default: 8090)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for persistent simulation results (default: output)",
    )
    args = parser.parse_args()

    create_app(
        topology_path=args.topology,
        trace_path=args.trace,
        output_dir=args.output_dir,
    )
    ui.run(
        title="MeshCore Workbench",
        port=args.port,
        reload=False,
        show=False,
    )


if __name__ == "__main__":
    main()
