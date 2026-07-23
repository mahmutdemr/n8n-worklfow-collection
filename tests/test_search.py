from __future__ import annotations

import json
from pathlib import Path

from n8n_workflow_search.node_icons import merge_icon_manifest
from n8n_workflow_search.search import (
    build_index,
    build_node_map,
    enrich_default_node_compatibility,
    enrich_metadata,
    enrich_node_counts,
    export_node_pages_index,
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
    key_stats_path = tmp_path / "all-possible-keys.csv"
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
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    key_counts: dict[str, int] = {}
    for definition in catalog:
        for key in definition:
            key_counts[key] = key_counts.get(key, 0) + 1
    key_stats_path.write_text(
        "key,item_count,usage_rate_percent\n"
        + "".join(
            f"{key},{count},{count * 100 / len(catalog):.2f}\n"
            for key, count in sorted(key_counts.items(), key=lambda item: (-item[1], item[0]))
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

    result = build_node_map(catalog_path, workflow_directory, output_path, key_stats_path)
    node_map = json.loads(output_path.read_text(encoding="utf-8"))
    nodes = {node["type"]: node for node in node_map["nodes"]}

    assert result.catalog_records == 3
    assert result.node_types == 2
    assert result.node_instances == 4
    assert node_map["summary"]["additionalCatalogRecordCount"] == 1
    assert node_map["summary"]["potentialKeyCount"] == len(key_counts)
    assert {item["key"] for item in node_map["potentialKeys"]} == set(key_counts)
    assert node_map["summary"]["unmappedNodeTypeCount"] == 1
    assert nodes["n8n-nodes-base.foo"]["catalog"]["availableVersions"] == ["2", "2.1", "1"]
    assert nodes["n8n-nodes-base.foo"]["catalog"]["keys"] == sorted(set(catalog[0]) | set(catalog[1]))
    assert nodes["n8n-nodes-base.foo"]["catalog"]["definitions"][0]["keys"] == sorted(catalog[0])
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


def test_node_pages_export_contains_search_metadata_without_local_sources(tmp_path: Path) -> None:
    map_path = tmp_path / "node-map.json"
    output_path = tmp_path / "pages" / "node-search-index.json"
    icon_output_path = tmp_path / "pages" / "node-icons"
    local_icon_path = tmp_path / "icons" / "n8n" / "foo.svg"
    local_icon_path.parent.mkdir(parents=True)
    local_icon_path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"/>", encoding="utf-8")
    map_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-23T00:00:00Z",
                "sources": {"workflowDirectory": "/private/workflows"},
                "summary": {"nodeTypeCount": 1, "usedNodeTypeCount": 1},
                "potentialKeys": [{"key": "credentials", "itemCount": 1, "usageRatePercent": 100}],
                "nodes": [
                    {
                        "type": "n8n-nodes-base.foo",
                        "packageName": "n8n-nodes-base",
                        "name": "foo",
                        "displayName": "Foo",
                        "description": "Test node",
                        "groups": ["transform"],
                        "categories": ["Data & Storage"],
                        "credentials": ["fooApi"],
                        "documentationUrls": ["https://example.test/foo"],
                        "icon": {
                            "light": "icons/n8n/foo.svg",
                            "dark": "icons/n8n/foo.svg",
                            "source": "n8n-design-system",
                            "fallback": False,
                        },
                        "usableAsTool": True,
                        "hidden": False,
                        "catalog": {
                            "definitionCount": 2,
                            "keys": ["credentials"],
                            "availableVersions": ["1", "2"],
                            "definitions": [{"keys": ["credentials"]}],
                        },
                        "usage": {
                            "workflowCount": 4,
                            "workflowPercentage": 20,
                            "instanceCount": 6,
                            "instancePercentage": 10,
                            "workflowRank": 1,
                            "instanceRank": 1,
                            "versions": [{"version": "2", "workflowCount": 4, "instanceCount": 6}],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert export_node_pages_index(map_path, output_path, icon_output_path) == 1
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    record = exported["nodes"][0]

    assert record["keys"] == ["credentials"]
    assert record["icon"] == {
        "light": "n8n/foo.svg",
        "dark": "n8n/foo.svg",
        "source": "n8n-design-system",
        "fallback": False,
    }
    assert record["usage"]["workflowCount"] == 4
    assert exported["schemaVersion"] == 2
    assert exported["iconBaseUrl"] == "../node-icons/"
    assert exported["iconFileCount"] == 1
    assert (icon_output_path / "n8n" / "foo.svg").is_file()
    assert "iconUrl" not in record
    assert "sources" not in exported
    assert "definitions" not in record


def test_merge_icon_manifest_enriches_every_node_and_rejects_mismatches(tmp_path: Path) -> None:
    manifest_path = tmp_path / "icons" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-23T00:00:00Z",
                "summary": {"nodeTypeCount": 2, "resolvedNodeTypeCount": 2},
                "nodes": {
                    "n8n-nodes-base.foo": {
                        "light": "icons/n8n/foo.svg",
                        "dark": "icons/n8n/foo.svg",
                        "source": "n8n-design-system",
                        "fallback": False,
                    },
                    "n8n-nodes-base.bar": {
                        "light": "icons/fallback.svg",
                        "dark": "icons/fallback.svg",
                        "source": "fallback",
                        "fallback": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    node_map = {
        "schemaVersion": 1,
        "nodes": [
            {"type": "n8n-nodes-base.foo"},
            {"type": "n8n-nodes-base.bar"},
        ],
    }

    merged = merge_icon_manifest(node_map, manifest_path)

    assert merged["schemaVersion"] == 2
    assert merged["nodes"][0]["icon"]["source"] == "n8n-design-system"
    assert merged["nodes"][1]["icon"]["fallback"] is True
    assert merged["nodeIconCatalog"]["summary"]["resolvedNodeTypeCount"] == 2

    merged["nodes"].append({"type": "n8n-nodes-base.extra"})
    try:
        merge_icon_manifest(merged, manifest_path)
    except ValueError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("A manifest/node-map mismatch must be rejected.")


def test_web_handler_binds_the_selected_paths(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)
    build_index(map_path, index_path)

    handler = create_handler(index_path, map_path)

    assert handler.__name__ == "WorkflowSearchHandler"
