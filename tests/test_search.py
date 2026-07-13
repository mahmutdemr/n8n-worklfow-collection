from __future__ import annotations

import json
from pathlib import Path

from n8n_workflow_search.search import build_index, get_stats, search


def _write_map(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-12T00:00:00Z",
                "workflows": [
                    {
                        "id": 1,
                        "name": "Send Slack alerts from Postgres",
                        "slug": "send-slack-alerts-postgres",
                        "views": 120,
                        "creator": {"name": "Ada Lovelace", "username": "ada"},
                        "categories": [{"name": "Engineering", "parent": {"name": "IT Ops"}}],
                        "galleryUrl": "https://example.test/1",
                        "file": "workflows/1.json",
                    },
                    {
                        "id": 2,
                        "name": "Create Notion pages",
                        "slug": "create-notion-pages",
                        "views": 20,
                        "creator": {"name": "Grace Hopper", "username": "grace"},
                        "categories": [{"name": "Marketing", "parent": None}],
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
    assert get_stats(index_path)["indexed_workflows"] == "2"


def test_search_any_mode_and_view_sort(tmp_path: Path) -> None:
    map_path = tmp_path / "workflow-map.json"
    index_path = tmp_path / "workflows.sqlite3"
    _write_map(map_path)
    build_index(map_path, index_path)

    results = search("notion slack", index_path=index_path, mode="any", sort="views")

    assert [result.id for result in results] == [1, 2]
