"""Command-line interface for the local workflow search index."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .search import DEFAULT_INDEX_PATH, DEFAULT_MAP_PATH, build_index, get_stats, resolved_local_file, search
from .web import serve


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fast local full-text search for n8n workflow metadata.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build", help="Build or refresh the metadata search index.")
    build.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path")
    build.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")

    query = subcommands.add_parser("search", help="Search workflow metadata.")
    query.add_argument("query", help="Words to search for")
    query.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path used to resolve local files")
    query.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")
    query.add_argument("--mode", choices=("all", "any"), default="all", help="Require all terms or allow any term")
    query.add_argument("--category", help="Case-insensitive category filter")
    query.add_argument("--creator", help="Case-insensitive creator name or username filter")
    query.add_argument("--min-views", type=int, help="Minimum n8n gallery views")
    query.add_argument("--limit", type=int, default=20, help="Maximum results (default: 20)")
    query.add_argument("--sort", choices=("rank", "views"), default="rank", help="Order by full-text rank or views")
    query.add_argument("--json", action="store_true", help="Print JSON instead of a readable list")

    stats = subcommands.add_parser("stats", help="Show index metadata.")
    stats.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")

    web = subcommands.add_parser("serve", help="Open the local browser search interface.")
    web.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path used to resolve local files")
    web.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")
    web.add_argument("--host", default="127.0.0.1", help="Host to listen on (default: 127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    return parser


def _print_results(results, map_path: Path) -> None:
    if not results:
        print("No workflows matched.")
        return
    for number, result in enumerate(results, start=1):
        creator = result.creator_name or result.creator_username or "Unknown"
        categories = result.categories or "Uncategorized"
        print(f"{number}. [{result.id}] {result.name}")
        print(f"   {categories} · {creator} · {result.views:,} views")
        print(f"   {result.gallery_url}")
        print(f"   {resolved_local_file(result, map_path)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            count = build_index(args.file, args.index)
            print(f"Indexed {count:,} workflows in {args.index}.")
        elif args.command == "search":
            results = search(
                args.query,
                index_path=args.index,
                mode=args.mode,
                category=args.category,
                creator=args.creator,
                min_views=args.min_views,
                limit=args.limit,
                sort=args.sort,
            )
            if args.json:
                print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
            else:
                _print_results(results, args.file)
        elif args.command == "stats":
            for key, value in get_stats(args.index).items():
                print(f"{key}: {value}")
        elif args.command == "serve":
            serve(index_path=args.index, map_path=args.file, host=args.host, port=args.port)
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
