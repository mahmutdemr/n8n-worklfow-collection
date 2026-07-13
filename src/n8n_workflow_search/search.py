"""SQLite FTS5 index creation and querying."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MAP_PATH = Path("collection/workflow-map.json")
DEFAULT_INDEX_PATH = Path(".n8n-search/workflows.sqlite3")


@dataclass(frozen=True)
class SearchResult:
    id: int
    name: str
    slug: str
    views: int
    node_count: int
    creator_name: str
    creator_username: str
    categories: str
    gallery_url: str
    file: str
    score: float | None = None


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    display_name: str | None
    parent_name: str | None
    workflow_count: int

    @property
    def label(self) -> str:
        return self.display_name or self.name


def _connect(index_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    return connection


def _category_text(categories: Iterable[dict[str, Any]]) -> str:
    labels: list[str] = []
    for category in categories:
        parent = category.get("parent") or {}
        for value in (category.get("displayName"), category.get("name"), parent.get("name")):
            if value and value not in labels:
                labels.append(str(value))
    return ", ".join(labels)


def _load_workflows(map_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        with map_path.open(encoding="utf-8") as source:
            payload = json.load(source)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Workflow map was not found: {map_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Workflow map is not valid JSON: {map_path} ({error})") from error

    workflows = payload.get("workflows") if isinstance(payload, dict) else None
    if not isinstance(workflows, list):
        raise ValueError("Workflow map must be an object containing a 'workflows' list.")
    return payload, workflows


def enrich_node_counts(map_path: Path = DEFAULT_MAP_PATH) -> tuple[int, int]:
    """Scan every local workflow and record its node count in the workflow map.

    The replacement is atomic: a missing or malformed workflow file leaves the
    original map untouched. Returns the workflow and total-node counts.
    """
    payload, workflows = _load_workflows(map_path)
    total_nodes = 0
    for workflow in workflows:
        workflow_path = map_path.parent / str(workflow["file"])
        try:
            with workflow_path.open(encoding="utf-8") as source:
                workflow_json = json.load(source)
        except FileNotFoundError as error:
            raise FileNotFoundError(f"Workflow file was not found: {workflow_path}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"Workflow file is not valid JSON: {workflow_path} ({error})") from error
        nodes = workflow_json.get("nodes") if isinstance(workflow_json, dict) else None
        if not isinstance(nodes, list):
            raise ValueError(f"Workflow has no 'nodes' list: {workflow_path}")
        workflow["nodeCount"] = len(nodes)
        total_nodes += len(nodes)

    payload["schemaVersion"] = max(int(payload.get("schemaVersion", 1)), 2)
    payload["nodeCountGeneratedAt"] = datetime.now(UTC).isoformat()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f".{map_path.name}.", suffix=".tmp", dir=map_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
    try:
        temporary_path.replace(map_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return len(workflows), total_nodes


def build_index(map_path: Path = DEFAULT_MAP_PATH, index_path: Path = DEFAULT_INDEX_PATH) -> int:
    """Build a new index atomically and return the number of indexed workflows."""
    payload, workflows = _load_workflows(map_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        prefix=f".{index_path.name}.", suffix=".tmp", dir=index_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        connection = _connect(temporary_path)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                CREATE TABLE workflow (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    views INTEGER NOT NULL,
                    node_count INTEGER NOT NULL,
                    creator_name TEXT NOT NULL,
                    creator_username TEXT NOT NULL,
                    categories TEXT NOT NULL,
                    gallery_url TEXT NOT NULL,
                    file TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE workflow_fts USING fts5(
                    name, slug, creator_name, creator_username, categories,
                    content='workflow', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE category (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    display_name TEXT,
                    parent_name TEXT
                );
                CREATE TABLE workflow_category (
                    workflow_id INTEGER NOT NULL REFERENCES workflow(id),
                    category_id INTEGER NOT NULL REFERENCES category(id),
                    PRIMARY KEY (workflow_id, category_id)
                );
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )

            rows = []
            for workflow in workflows:
                creator = workflow.get("creator") or {}
                rows.append(
                    (
                        workflow["id"],
                        str(workflow.get("name") or ""),
                        str(workflow.get("slug") or ""),
                        int(workflow.get("views") or 0),
                        int(workflow.get("nodeCount") or 0),
                        str(creator.get("name") or ""),
                        str(creator.get("username") or ""),
                        _category_text(workflow.get("categories") or []),
                        str(workflow.get("galleryUrl") or ""),
                        str(workflow.get("file") or ""),
                    )
                )
            connection.executemany(
                """
                INSERT INTO workflow (
                    id, name, slug, views, node_count, creator_name, creator_username, categories, gallery_url, file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            category_rows = []
            for category in payload.get("categories", []):
                parent = category.get("parent") or {}
                category_rows.append(
                    (
                        int(category["id"]),
                        str(category.get("name") or ""),
                        category.get("displayName"),
                        parent.get("name"),
                    )
                )
            connection.executemany(
                "INSERT INTO category (id, name, display_name, parent_name) VALUES (?, ?, ?, ?)",
                category_rows,
            )
            workflow_categories = {
                (int(workflow["id"]), int(category["id"]))
                for workflow in workflows
                for category in workflow.get("categories") or []
                if category.get("id") is not None
            }
            connection.executemany(
                "INSERT INTO workflow_category (workflow_id, category_id) VALUES (?, ?)", workflow_categories
            )
            connection.execute("INSERT INTO workflow_fts(workflow_fts) VALUES ('rebuild')")
            metadata = {
                "indexed_workflows": str(len(rows)),
                "schema_version": str(payload.get("schemaVersion", "unknown")),
                "map_generated_at": str(payload.get("generatedAt", "unknown")),
                "map_path": str(map_path),
            }
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
            connection.commit()
        finally:
            connection.close()
        temporary_path.replace(index_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return len(workflows)


def _fts_query(query: str, mode: str) -> str:
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        raise ValueError("Search query must include at least one letter or number.")
    quoted = [f'"{word.replace(chr(34), "")}"' for word in words]
    separator = " OR " if mode == "any" else " "
    return separator.join(quoted)


def search(
    query: str,
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    mode: str = "all",
    category: str | None = None,
    category_id: int | None = None,
    creator: str | None = None,
    min_views: int | None = None,
    min_nodes: int | None = None,
    max_nodes: int | None = None,
    limit: int = 20,
    sort: str = "rank",
) -> list[SearchResult]:
    """Search indexed metadata and return the best matching workflows."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Search index was not found: {index_path}. Run 'n8n-search build' first.")
    if mode not in {"all", "any"}:
        raise ValueError("mode must be 'all' or 'any'.")
    if sort not in {"rank", "views"}:
        raise ValueError("sort must be 'rank' or 'views'.")
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if min_nodes is not None and min_nodes < 0:
        raise ValueError("min_nodes cannot be negative.")
    if max_nodes is not None and max_nodes < 0:
        raise ValueError("max_nodes cannot be negative.")
    if min_nodes is not None and max_nodes is not None and min_nodes > max_nodes:
        raise ValueError("min_nodes cannot be greater than max_nodes.")

    clauses = ["workflow_fts MATCH ?"]
    parameters: list[Any] = [_fts_query(query, mode)]
    if category:
        clauses.append("workflow.categories LIKE ? COLLATE NOCASE")
        parameters.append(f"%{category}%")
    if category_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM workflow_category WHERE workflow_category.workflow_id = workflow.id "
            "AND workflow_category.category_id = ?)"
        )
        parameters.append(category_id)
    if creator:
        clauses.append("(workflow.creator_name LIKE ? COLLATE NOCASE OR workflow.creator_username LIKE ? COLLATE NOCASE)")
        parameters.extend((f"%{creator}%", f"%{creator}%"))
    if min_views is not None:
        clauses.append("workflow.views >= ?")
        parameters.append(min_views)
    if min_nodes is not None:
        clauses.append("workflow.node_count >= ?")
        parameters.append(min_nodes)
    if max_nodes is not None:
        clauses.append("workflow.node_count <= ?")
        parameters.append(max_nodes)

    order_by = "workflow.views DESC, workflow.name COLLATE NOCASE" if sort == "views" else "score, workflow.views DESC"
    sql = f"""
        SELECT workflow.*, bm25(workflow_fts, 10.0, 4.0, 2.0, 2.0, 1.0) AS score
        FROM workflow_fts
        JOIN workflow ON workflow_fts.rowid = workflow.id
        WHERE {' AND '.join(clauses)}
        ORDER BY {order_by}
        LIMIT ?
    """
    parameters.append(limit)
    with _connect(index_path) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [SearchResult(**dict(row)) for row in rows]


def get_stats(index_path: Path = DEFAULT_INDEX_PATH) -> dict[str, str]:
    if not index_path.is_file():
        raise FileNotFoundError(f"Search index was not found: {index_path}. Run 'n8n-search build' first.")
    with _connect(index_path) as connection:
        values = connection.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
        count = connection.execute("SELECT COUNT(*) FROM workflow").fetchone()[0]
    return {"indexed_workflows": str(count), **{row["key"]: row["value"] for row in values}}


def get_categories(index_path: Path = DEFAULT_INDEX_PATH) -> list[Category]:
    """Return every map category and its direct workflow membership count."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Search index was not found: {index_path}. Run 'n8n-search build' first.")
    with _connect(index_path) as connection:
        rows = connection.execute(
            """
            SELECT category.id, category.name, category.display_name, category.parent_name,
                   COUNT(workflow_category.workflow_id) AS workflow_count
            FROM category
            LEFT JOIN workflow_category ON workflow_category.category_id = category.id
            GROUP BY category.id
            ORDER BY workflow_count DESC, COALESCE(category.display_name, category.name) COLLATE NOCASE
            """
        ).fetchall()
    return [Category(**dict(row)) for row in rows]


def resolved_local_file(result: SearchResult, map_path: Path = DEFAULT_MAP_PATH) -> Path:
    """Return the archive path for a result relative to the selected map's parent."""
    return (map_path.parent / result.file).resolve()
