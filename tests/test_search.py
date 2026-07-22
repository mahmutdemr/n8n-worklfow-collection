from __future__ import annotations

import json
from pathlib import Path

from n8n_workflow_search.search import (
    build_index,
    build_node_map,
    enrich_default_node_compatibility,
    enrich_metadata,
    enrich_node_counts,
    export_pages_index,
    get_categories,
    get_stats,
    search,
    search_page,
)
from n8n_workflow_search.web import create_handler


def _write_map(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-12T00:00:00Z",
                "categories": [
                    {"id": 5, "name": "Engineering", "displayName": None, "parent": {"name": "IT Ops"}, "workflowIds": [1]},
                    {"id": 27, "name": "Marketing", "displayName": None, "parent": None, "workflowIds": [2]},
                ],
                "workflows": [
                    {
                        "id": 1,
                        "name": "Send Slack alerts from Postgres",
                        "slug": "send-slack-alerts-postgres",
                        "views": 120,
                        "nodeCount": 3,
                        "creator": {"name": "Ada Lovelace", "username": "ada"},
                        "categories": [{"id": 5, "name": "Engineering", "parent": {"name": "IT Ops"}}],
                        "galleryUrl": "https://example.test/1",
                        "file": "workflows/1.json",
                    },
                    {
                        "id": 2,
                        "name": "Create Notion pages",
                        "slug": "create-notion-pages",
                        "views": 20,
                        "nodeCount": 12,
                        "creator": {"name": "Grace Hopper", "username": "grace"},
                        "categories": [{"id": 27, "name": "Marketing", "parent": None}],
                        "galleryUrl": "https://example.test/2",
                        "file": "workflows/2.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_build_and_search_metadata(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)

    assert build_index(map_path, index_path) == 2
    results = search("postgres slack", index_path=index_path, category="IT Ops")

    assert [result.id for result in results] == [1]
    assert results[0].creator_username == "ada"
    assert results[0].node_count == 3
    assert get_stats(index_path)["indexed_workflows"] == "2"
    assert [(category.id, category.workflow_count) for category in get_categories(index_path)] == [(5, 1), (27, 1)]


def test_search_any_mode_and_view_sort(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)
    build_index(map_path, index_path)

    results = search("notion slack", index_path=index_path, mode="any", sort="views")

    assert [result.id for result in results] == [1, 2]

    results = search("notion slack", index_path=index_path, mode="any", sort="nodes")

    assert [result.id for result in results] == [2, 1]

    page = search_page(index_path=index_path, limit=1, offset=1, sort="nodes")

    assert page.total == 2
    assert [result.id for result in page.results] == [1]


def test_node_range_filter_and_map_enrichment(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    workflow_directory = tmp_path / "workflows"
    workflow_directory.mkdir()
    _write_map(map_path)
    (workflow_directory / "1.json").write_text('{"nodes": [{}, {}, {}, {}]}', encoding="utf-8")
    (workflow_directory / "2.json").write_text('{"nodes": [{}]}', encoding="utf-8")

    assert enrich_node_counts(map_path) == (2, 5)
    build_index(map_path, index_path)
    assert [result.id for result in search("slack", index_path=index_path, min_nodes=4)] == [1]


def test_v2_metadata_enrichment_preserves_node_count(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    v2_path = tmp_path / "workflow-map-v2.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)
    source = json.loads(map_path.read_text(encoding="utf-8"))
    source["generatedAt"] = "2026-07-13T00:00:00Z"
    for workflow in source["workflows"]:
        workflow.update(
            {
                "description": f"Detailed description for {workflow['name']}",
                "createdAt": "2025-01-02T03:04:05Z",
                "updatedAt": "2025-02-03T04:05:06Z",
                "lastSeenAt": "2026-07-13T00:00:00Z",
                "downloadedAt": None,
                "popularity": {"views": workflow["views"] + 100, "recentViews": 0},
            }
        )
    v2_path.write_text(json.dumps(source), encoding="utf-8")

    assert enrich_metadata(map_path, v2_path) == 2
    build_index(map_path, index_path)
    results = search("detailed slack", index_path=index_path)

    assert results[0].node_count == 3
    assert results[0].views == 220
    assert results[0].created_at == "2025-01-02T03:04:05Z"
    assert search_page(index_path=index_path, created_after="2025-01-01").total == 2
    assert search_page(index_path=index_path, created_after="2025-02-01").total == 0


def test_default_node_compatibility_tags_and_filters(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    catalog_path = tmp_path / "n8n-nodes.json"
    index_path = tmp_path / "workflows.sqlite3"
    workflow_directory = tmp_path / "workflows"
    workflow_directory.mkdir()
    _write_map(map_path)
    catalog_path.write_text(
        json.dumps([{"name": "n8n-nodes-base.slack"}, {"name": "n8n-nodes-base.notion"}]), encoding="utf-8"
    )
    (workflow_directory / "1.json").write_text(
        json.dumps({"nodes": [{"type": "n8n-nodes-base.slack"}, {"type": "n8n-nodes-extra.foo"}, {"type": "n8n-nodes-extra.foo"}]}),
        encoding="utf-8",
    )
    (workflow_directory / "2.json").write_text(
        json.dumps({"nodes": [{"type": "n8n-nodes-base.notion"}]}), encoding="utf-8"
    )

    summary = enrich_default_node_compatibility(map_path, catalog_path)
    build_index(map_path, index_path)

    assert summary.compatible_workflows == 1
    assert summary.unavailable_node_types == 1
    assert [result.id for result in search(index_path=index_path, default_compatible=True)] == [2]
    missing = search(index_path=index_path, default_compatible=False)
    assert missing[0].missing_node_type_count == 1
    assert missing[0].missing_node_instance_count == 2
    assert json.loads(missing[0].missing_node_packages) == ["n8n-nodes-extra"]


def test_build_node_map_aggregates_types_workflows_instances_and_versions(tmp_path: Path) -> None:
    catalog_path = tmp_path / "n8n-nodes.json"
    workflow_directory = tmp_path / "workflows"
    output_path = tmp_path / "nodes" / "node-map.json"
    workflow_directory.mkdir()
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "name": "n8n-nodes-base.foo",
                    "displayName": "Foo",
                    "description": "Foo node",
                    "version": [2, 2.1],
                    "group": ["transform"],
                    "credentials": [{"name": "fooApi", "required": True}],
                    "codex": {"categories": ["Data & Storage"]},
                    "inputs": [],
                    "outputs": [],
                    "properties": [],
                    "defaults": {},
                },
                {
                    "name": "n8n-nodes-base.foo",
                    "displayName": "Foo",
                    "description": "Old Foo node",
                    "version": 1,
                    "group": ["transform"],
                    "inputs": [],
                    "outputs": [],
                    "properties": [],
                    "defaults": {},
                },
                {
                    "name": "n8n-nodes-base.bar",
                    "displayName": "Bar",
                    "description": "Bar node",
                    "version": 1,
                    "group": ["output"],
                    "inputs": [],
                    "outputs": [],
                    "properties": [],
                    "defaults": {},
                },
            ]
        ),
        encoding="utf-8",
    )
    (workflow_directory / "one.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {"type": "n8n-nodes-base.foo", "typeVersion": 1},
                    {"type": "n8n-nodes-base.foo", "typeVersion": 1, "disabled": True},
                    {"type": "n8n-nodes-extra.baz", "typeVersion": 3},
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_directory / "two.json").write_text(
        json.dumps({"nodes": [{"type": "n8n-nodes-base.foo", "typeVersion": 2.1}]}), encoding="utf-8"
    )

    result = build_node_map(catalog_path, workflow_directory, output_path)
    node_map = json.loads(output_path.read_text(encoding="utf-8"))
    nodes = {node["type"]: node for node in node_map["nodes"]}

    assert result.catalog_records == 3
    assert result.node_types == 2
    assert result.node_instances == 4
    assert node_map["summary"]["additionalCatalogRecordCount"] == 1
    assert node_map["summary"]["unmappedNodeTypeCount"] == 1
    assert nodes["n8n-nodes-base.foo"]["catalog"]["availableVersions"] == ["2", "2.1", "1"]
    assert nodes["n8n-nodes-base.foo"]["usage"]["workflowCount"] == 2
    assert nodes["n8n-nodes-base.foo"]["usage"]["instanceCount"] == 3
    assert nodes["n8n-nodes-base.foo"]["usage"]["disabledInstanceCount"] == 1
    assert nodes["n8n-nodes-base.foo"]["usage"]["versions"] == [
        {"version": "1", "workflowCount": 1, "instanceCount": 2},
        {"version": "2.1", "workflowCount": 1, "instanceCount": 1},
    ]
    assert nodes["n8n-nodes-base.bar"]["usage"]["instanceCount"] == 0
    assert node_map["unmappedNodeTypes"][0]["type"] == "n8n-nodes-extra.baz"


def test_pages_export_contains_only_public_search_metadata(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    output_path = tmp_path / "pages" / "search-index.json"
    _write_map(map_path)
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    payload["workflows"][0]["defaultNodeCompatibility"] = {
        "usesOnlyInstalledDefaultNodes": False,
        "missingNodeTypeCount": 1,
        "missingNodeInstanceCount": 2,
        "missingNodeTypes": ["n8n-nodes-extra.foo"],
        "missingNodePackages": ["n8n-nodes-extra"],
    }
    map_path.write_text(json.dumps(payload), encoding="utf-8")

    assert export_pages_index(map_path, output_path) == 2
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    record = exported["workflows"][0]

    assert record["missingNodeTypes"] == ["n8n-nodes-extra.foo"]
    assert "file" not in record
    assert "workflowIds" not in exported["categories"][0]


def test_web_handler_binds_the_selected_paths(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)
    build_index(map_path, index_path)

    handler = create_handler(index_path, map_path)

    assert handler.__name__ == "WorkflowSearchHandler"
