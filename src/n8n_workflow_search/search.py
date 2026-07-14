"""SQLite FTS5 index creation and querying."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MAP_PATH = Path("collection/workflow-map.json")
DEFAULT_V2_MAP_PATH = Path("collection/workflow-map-v2.json")
DEFAULT_NODE_CATALOG_PATH = Path("collection/n8n-nodes.json")
DEFAULT_INDEX_PATH = Path(".n8n-search/workflows.sqlite3")


@dataclass(frozen=True)
class SearchResult:
    id: int
    name: str
    slug: str
    views: int
    node_count: int
    description: str
    created_at: str
    updated_at: str
    last_seen_at: str
    default_compatible: int | None
    missing_node_type_count: int
    missing_node_instance_count: int
    missing_node_types: str
    missing_node_packages: str
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


@dataclass(frozen=True)
class SearchPage:
    results: list[SearchResult]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class CompatibilitySummary:
    workflows: int
    compatible_workflows: int
    unavailable_node_types: int


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


def _write_map_atomic(payload: dict[str, Any], map_path: Path) -> None:
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
    _write_map_atomic(payload, map_path)
    return len(workflows), total_nodes


def enrich_metadata(
    map_path: Path = DEFAULT_MAP_PATH, source_path: Path = DEFAULT_V2_MAP_PATH
) -> int:
    """Merge the detailed v2 metadata map into the primary map by workflow id.

    Locally calculated fields such as ``nodeCount`` are retained. The operation
    is atomic and refuses a partial merge when either map has unmatched ids.
    """
    payload, workflows = _load_workflows(map_path)
    source_payload, source_workflows = _load_workflows(source_path)
    current_by_id = {workflow["id"]: workflow for workflow in workflows}
    source_by_id = {workflow["id"]: workflow for workflow in source_workflows}
    if len(current_by_id) != len(workflows) or len(source_by_id) != len(source_workflows):
        raise ValueError("Workflow ids must be unique in both maps.")
    if current_by_id.keys() != source_by_id.keys():
        missing_in_source = len(current_by_id.keys() - source_by_id.keys())
        missing_in_target = len(source_by_id.keys() - current_by_id.keys())
        raise ValueError(
            f"Maps do not contain the same workflow ids ({missing_in_source} missing in source, "
            f"{missing_in_target} missing in target)."
        )

    for workflow_id, target in current_by_id.items():
        node_count = target.get("nodeCount")
        target.update(source_by_id[workflow_id])
        if node_count is not None:
            target["nodeCount"] = node_count
        popularity = target.get("popularity") or {}
        if isinstance(popularity.get("views"), int):
            target["views"] = popularity["views"]

    for key in (
        "generatedAt",
        "archiveWorkflowCount",
        "unavailableWorkflowCount",
        "categories",
        "uncategorizedWorkflowIds",
        "unavailableWorkflows",
    ):
        if key in source_payload:
            payload[key] = source_payload[key]
    payload["schemaVersion"] = max(int(payload.get("schemaVersion", 1)), int(source_payload.get("schemaVersion", 1)), 3)
    payload["metadataEnrichedAt"] = datetime.now(UTC).isoformat()
    payload["metadataSource"] = str(source_path)
    _write_map_atomic(payload, map_path)
    return len(workflows)


def _node_package_name(node_type: str) -> str:
    """Extract the package portion from an n8n node type identifier."""
    if node_type.startswith("@"):
        scope, _, remainder = node_type.partition("/")
        package, _, _ = remainder.partition(".")
        return f"{scope}/{package}" if package else node_type
    return node_type.partition(".")[0]


def enrich_default_node_compatibility(
    map_path: Path = DEFAULT_MAP_PATH, node_catalog_path: Path = DEFAULT_NODE_CATALOG_PATH
) -> CompatibilitySummary:
    """Tag every workflow against the node types installed in a default catalog."""
    payload, workflows = _load_workflows(map_path)
    try:
        catalog_bytes = node_catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Node catalog was not found: {node_catalog_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Node catalog is not valid JSON: {node_catalog_path} ({error})") from error
    if not isinstance(catalog, list):
        raise ValueError("Node catalog must be a JSON list.")
    available_types = {item.get("name") for item in catalog if isinstance(item, dict) and item.get("name")}
    if not available_types:
        raise ValueError("Node catalog does not contain any node names.")

    compatible_workflows = 0
    unavailable_types: set[str] = set()
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
        node_types = [node.get("type") for node in nodes if isinstance(node, dict) and node.get("type")]
        missing_types = sorted(set(node_types) - available_types)
        missing_type_set = set(missing_types)
        missing_instance_count = sum(node_type in missing_type_set for node_type in node_types)
        compatible = not missing_types
        if compatible:
            compatible_workflows += 1
        unavailable_types.update(missing_types)
        workflow["defaultNodeCompatibility"] = {
            "usesOnlyInstalledDefaultNodes": compatible,
            "missingNodeTypeCount": len(missing_types),
            "missingNodeInstanceCount": missing_instance_count,
            "missingNodeTypes": missing_types,
            "missingNodePackages": sorted({_node_package_name(node_type) for node_type in missing_types}),
        }

    payload["schemaVersion"] = max(int(payload.get("schemaVersion", 1)), 4)
    payload["defaultNodeCatalog"] = {
        "path": str(node_catalog_path),
        "nodeTypeCount": len(available_types),
        "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "checkedAt": datetime.now(UTC).isoformat(),
    }
    _write_map_atomic(payload, map_path)
    return CompatibilitySummary(len(workflows), compatible_workflows, len(unavailable_types))


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
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    default_compatible INTEGER,
                    missing_node_type_count INTEGER NOT NULL,
                    missing_node_instance_count INTEGER NOT NULL,
                    missing_node_types TEXT NOT NULL,
                    missing_node_packages TEXT NOT NULL,
                    creator_name TEXT NOT NULL,
                    creator_username TEXT NOT NULL,
                    categories TEXT NOT NULL,
                    gallery_url TEXT NOT NULL,
                    file TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE workflow_fts USING fts5(
                    name, slug, creator_name, creator_username, categories, description, missing_node_types, missing_node_packages,
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
                compatibility = workflow.get("defaultNodeCompatibility") or {}
                compatible = compatibility.get("usesOnlyInstalledDefaultNodes")
                rows.append(
                    (
                        workflow["id"],
                        str(workflow.get("name") or ""),
                        str(workflow.get("slug") or ""),
                        int(workflow.get("views") or 0),
                        int(workflow.get("nodeCount") or 0),
                        str(workflow.get("description") or ""),
                        str(workflow.get("createdAt") or ""),
                        str(workflow.get("updatedAt") or ""),
                        str(workflow.get("lastSeenAt") or ""),
                        int(compatible) if isinstance(compatible, bool) else None,
                        int(compatibility.get("missingNodeTypeCount") or 0),
                        int(compatibility.get("missingNodeInstanceCount") or 0),
                        json.dumps(compatibility.get("missingNodeTypes") or []),
                        json.dumps(compatibility.get("missingNodePackages") or []),
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
                    id, name, slug, views, node_count, description, created_at, updated_at, last_seen_at,
                    default_compatible, missing_node_type_count, missing_node_instance_count,
                    missing_node_types, missing_node_packages, creator_name, creator_username, categories, gallery_url, file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _date_bound(value: str | None, name: str, *, end: bool = False) -> tuple[str | None, bool]:
    """Validate an ISO date or datetime and mark date-only end bounds as exclusive."""
    if not value:
        return None, False
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{name} must be an ISO date such as 2025-07-13.") from error
        return value, False
    if end:
        return f"{parsed_date + timedelta(days=1):%Y-%m-%d}T00:00:00.000Z", True
    return f"{parsed_date:%Y-%m-%d}T00:00:00.000Z", False


def search_page(
    query: str | None = None,
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    mode: str = "all",
    category: str | None = None,
    category_id: int | None = None,
    creator: str | None = None,
    min_views: int | None = None,
    min_nodes: int | None = None,
    max_nodes: int | None = None,
    default_compatible: bool | None = None,
    min_missing_node_types: int | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "rank",
) -> SearchPage:
    """Search metadata and return one result page and its total size."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Search index was not found: {index_path}. Run 'n8n-search build' first.")
    if mode not in {"all", "any"}:
        raise ValueError("mode must be 'all' or 'any'.")
    if sort not in {"rank", "views", "nodes"}:
        raise ValueError("sort must be 'rank', 'views', or 'nodes'.")
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if offset < 0:
        raise ValueError("offset cannot be negative.")
    if min_nodes is not None and min_nodes < 0:
        raise ValueError("min_nodes cannot be negative.")
    if max_nodes is not None and max_nodes < 0:
        raise ValueError("max_nodes cannot be negative.")
    if min_nodes is not None and max_nodes is not None and min_nodes > max_nodes:
        raise ValueError("min_nodes cannot be greater than max_nodes.")
    if min_missing_node_types is not None and min_missing_node_types < 0:
        raise ValueError("min_missing_node_types cannot be negative.")

    text_query = (query or "").strip()
    has_text_query = bool(text_query)
    clauses: list[str] = []
    parameters: list[Any] = []
    if has_text_query:
        clauses.append("workflow_fts MATCH ?")
        parameters.append(_fts_query(text_query, mode))
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
    if default_compatible is not None:
        clauses.append("workflow.default_compatible = ?")
        parameters.append(int(default_compatible))
    if min_missing_node_types is not None:
        clauses.append("workflow.missing_node_type_count >= ?")
        parameters.append(min_missing_node_types)
    after_bound, _ = _date_bound(created_after, "created_after")
    before_bound, before_is_date = _date_bound(created_before, "created_before", end=True)
    if after_bound:
        clauses.append("workflow.created_at >= ?")
        parameters.append(after_bound)
    if before_bound:
        clauses.append("workflow.created_at < ?" if before_is_date else "workflow.created_at <= ?")
        parameters.append(before_bound)

    order_by = {
        "rank": "score, workflow.views DESC" if has_text_query else "workflow.views DESC, workflow.node_count DESC",
        "views": "workflow.views DESC, workflow.node_count DESC, workflow.name COLLATE NOCASE",
        "nodes": "workflow.node_count DESC, workflow.views DESC, workflow.name COLLATE NOCASE",
    }[sort]
    source = "workflow_fts JOIN workflow ON workflow_fts.rowid = workflow.id" if has_text_query else "workflow"
    score = "bm25(workflow_fts, 10.0, 4.0, 2.0, 2.0, 1.0, 2.0, 1.0, 1.0)" if has_text_query else "NULL"
    where = " AND ".join(clauses) or "1 = 1"
    sql = f"""
        SELECT workflow.*, {score} AS score
        FROM {source}
        WHERE {where}
        ORDER BY {order_by}
        LIMIT ?
        OFFSET ?
    """
    with _connect(index_path) as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM {source} WHERE {where}", parameters).fetchone()[0]
        rows = connection.execute(sql, [*parameters, limit, offset]).fetchall()
    return SearchPage([SearchResult(**dict(row)) for row in rows], total, offset, limit)


def search(query: str | None = None, **kwargs: Any) -> list[SearchResult]:
    """Return one page of matching workflows for command-line callers."""
    return search_page(query, **kwargs).results


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
