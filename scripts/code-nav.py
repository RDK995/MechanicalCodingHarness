#!/usr/bin/env python3
"""Return bounded code locations using LSP first and exact text as fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harnesslib.navigation import CodeNavigator
from harnesslib.state import HarnessError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path.cwd())
    result.add_argument("--timeout", type=float, default=5.0)
    result.add_argument("--max-results", type=int, default=40)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="show available LSP or fallback per language")
    for operation in ("definition", "references"):
        query = commands.add_parser(operation, help=f"locate symbol {operation} locations")
        query.add_argument("--file", required=True, type=Path)
        query.add_argument("--line", required=True, type=int)
        query.add_argument("--column", required=True, type=int)
        query.add_argument("--symbol")
        query.add_argument("--path", action="append", type=Path, default=[])
        if operation == "references":
            query.add_argument("--include-declaration", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        navigator = CodeNavigator(
            args.root, timeout=args.timeout, max_results=args.max_results
        )
        if args.command == "capabilities":
            output = navigator.capabilities()
        else:
            output = navigator.query(
                args.command,
                args.file,
                args.line,
                args.column,
                symbol=args.symbol,
                paths=args.path or None,
                include_declaration=getattr(args, "include_declaration", False),
            )
        print(json.dumps(output, sort_keys=True))
        return 0
    except (HarnessError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
