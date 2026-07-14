"""Command-line interface for the local workflow search index."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .search import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MAP_PATH,
    DEFAULT_NODE_CATALOG_PATH,
    DEFAULT_PAGES_INDEX_PATH,
    DEFAULT_V2_MAP_PATH,
    build_index,
    enrich_default_node_compatibility,
    enrich_metadata,
    enrich_node_counts,
    get_categories,
    get_stats,
    resolved_local_file,
    search,
    export_pages_index,
)
from .web import serve


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fast local full-text search for n8n workflow metadata.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build", help="Build or refresh the metadata search index.")
    build.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path")
    build.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")

    enrich = subcommands.add_parser("enrich-node-counts", help="Scan workflow JSON files and add nodeCount to the map.")
    enrich.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path")
    enrich.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path to rebuild")

    metadata = subcommands.add_parser("enrich-metadata", help="Merge detailed metadata from workflow-map-v2.json.")
    metadata.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="primary workflow map path")
    metadata.add_argument("--source", type=_path, default=DEFAULT_V2_MAP_PATH, help="detailed v2 map path")
    metadata.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path to rebuild")

    compatibility = subcommands.add_parser(
        "enrich-default-node-compatibility", help="Tag workflows against the installed default node catalog."
    )
    compatibility.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="primary workflow map path")
    compatibility.add_argument("--catalog", type=_path, default=DEFAULT_NODE_CATALOG_PATH, help="installed node catalog path")
    compatibility.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path to rebuild")

    export = subcommands.add_parser("export-pages", help="Export the minimal public index used by the GitHub Pages site.")
    export.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="primary workflow map path")
    export.add_argument("--output", type=_path, default=DEFAULT_PAGES_INDEX_PATH, help="public JSON index path")

    query = subcommands.add_parser("search", help="Search workflow metadata.")
    query.add_argument("query", nargs="?", default="", help="Optional words to search for")
    query.add_argument("--file", type=_path, default=DEFAULT_MAP_PATH, help="workflow-map.json path used to resolve local files")
    query.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")
    query.add_argument("--mode", choices=("all", "any"), default="all", help="Require all terms or allow any term")
    query.add_argument("--category", help="Case-insensitive category filter")
    query.add_argument("--category-id", type=int, help="Exact category id from 'n8n-search categories'")
    query.add_argument("--creator", help="Case-insensitive creator name or username filter")
    query.add_argument("--min-views", type=int, help="Minimum n8n gallery views")
    query.add_argument("--min-nodes", type=int, help="Minimum workflow node count")
    query.add_argument("--max-nodes", type=int, help="Maximum workflow node count")
    query.add_argument(
        "--default-compatible", action=argparse.BooleanOptionalAction, default=None,
        help="Require only installed-default nodes, or use --no-default-compatible for unavailable nodes",
    )
    query.add_argument("--min-missing-node-types", type=int, help="Minimum unavailable node-type count")
    query.add_argument("--created-after", help="Only workflows created on or after this ISO date (YYYY-MM-DD)")
    query.add_argument("--created-before", help="Only workflows created on or before this ISO date (YYYY-MM-DD)")
    query.add_argument("--limit", type=int, default=20, help="Maximum results (default: 20)")
    query.add_argument("--sort", choices=("rank", "views", "nodes"), default="rank", help="Order by full-text rank, views, or nodes")
    query.add_argument("--json", action="store_true", help="Print JSON instead of a readable list")

    stats = subcommands.add_parser("stats", help="Show index metadata.")
    stats.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")

    categories = subcommands.add_parser("categories", help="List map categories and workflow usage counts.")
    categories.add_argument("--index", type=_path, default=DEFAULT_INDEX_PATH, help="SQLite index path")
    categories.add_argument("--json", action="store_true", help="Print JSON instead of a readable list")

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
        elif args.command == "enrich-node-counts":
            workflow_count, total_nodes = enrich_node_counts(args.file)
            build_index(args.file, args.index)
            print(f"Added nodeCount for {workflow_count:,} workflows ({total_nodes:,} total nodes) and rebuilt {args.index}.")
        elif args.command == "enrich-metadata":
            workflow_count = enrich_metadata(args.file, args.source)
            build_index(args.file, args.index)
            print(f"Merged v2 metadata for {workflow_count:,} workflows and rebuilt {args.index}.")
        elif args.command == "enrich-default-node-compatibility":
            summary = enrich_default_node_compatibility(args.file, args.catalog)
            build_index(args.file, args.index)
            print(
                f"Tagged {summary.workflows:,} workflows: {summary.compatible_workflows:,} use only installed default nodes; "
                f"{summary.unavailable_node_types:,} unavailable node types found."
            )
        elif args.command == "export-pages":
            count = export_pages_index(args.file, args.output)
            print(f"Exported {count:,} workflows to {args.output}.")
        elif args.command == "search":
            results = search(
                args.query,
                index_path=args.index,
                mode=args.mode,
                category=args.category,
                category_id=args.category_id,
                creator=args.creator,
                min_views=args.min_views,
                min_nodes=args.min_nodes,
                max_nodes=args.max_nodes,
                default_compatible=args.default_compatible,
                min_missing_node_types=args.min_missing_node_types,
                created_after=args.created_after,
                created_before=args.created_before,
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
        elif args.command == "categories":
            categories = get_categories(args.index)
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "id": category.id,
                                "label": category.label,
                                "parent_name": category.parent_name,
                                "workflow_count": category.workflow_count,
                            }
                            for category in categories
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for category in categories:
                    parent = f" · {category.parent_name}" if category.parent_name else ""
                    print(f"[{category.id}] {category.label}: {category.workflow_count:,}{parent}")
        elif args.command == "serve":
            serve(index_path=args.index, map_path=args.file, host=args.host, port=args.port)
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
