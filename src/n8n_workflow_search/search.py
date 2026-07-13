"""SQLite FTS5 index creation and querying."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
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
    creator_name: str
    creator_username: str
    categories: str
    gallery_url: str
    file: str
    score: float | None = None


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
                    id, name, slug, views, creator_name, creator_username, categories, gallery_url, file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
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
    creator: str | None = None,
    min_views: int | None = None,
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

    clauses = ["workflow_fts MATCH ?"]
    parameters: list[Any] = [_fts_query(query, mode)]
    if category:
        clauses.append("workflow.categories LIKE ? COLLATE NOCASE")
        parameters.append(f"%{category}%")
    if creator:
        clauses.append("(workflow.creator_name LIKE ? COLLATE NOCASE OR workflow.creator_username LIKE ? COLLATE NOCASE)")
        parameters.extend((f"%{creator}%", f"%{creator}%"))
    if min_views is not None:
        clauses.append("workflow.views >= ?")
        parameters.append(min_views)

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


def resolved_local_file(result: SearchResult, map_path: Path = DEFAULT_MAP_PATH) -> Path:
    """Return the archive path for a result relative to the selected map's parent."""
    return (map_path.parent / result.file).resolve()
