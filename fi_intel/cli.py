"""Command-line entry point for the project shell."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from fi_intel import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fi-intel")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status", help="Show the scaffold status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "status":
        print(
            json.dumps(
                {
                    "name": "fi-intel",
                    "stage": "scaffold",
                    "status": "ready",
                    "version": __version__,
                },
                sort_keys=True,
            )
        )
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
