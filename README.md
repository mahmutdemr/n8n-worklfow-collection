# n8n workflow search

This repository provides a local, fast search interface for the workflow archive in
`collection/`. The archive itself and the generated index are intentionally ignored by Git.

## Quick start

```bash
uv sync
uv run n8n-search build
uv run n8n-search enrich-node-counts
uv run n8n-search enrich-metadata
uv run n8n-search enrich-default-node-compatibility
uv run n8n-search search "postgres slack"
uv run n8n-search serve
```

The `build` command reads `collection/workflow-map.json` and creates
`.n8n-search/workflows.sqlite3`. It only indexes map metadata, so building is quick even
though the archive contains more than 10,000 workflow JSON files. Re-run it whenever the
map is updated.

`enrich-node-counts` scans every local workflow JSON file, records a `nodeCount`
on each workflow in the map, and rebuilds the search index. Once enriched, node
range filters are available in the browser and from the command line:

```bash
uv run n8n-search search "slack" --min-nodes 5 --max-nodes 20
uv run n8n-search search --created-after 2025-07-13 --sort nodes
```

`enrich-metadata` merges the detailed `collection/workflow-map-v2.json` data
into the primary map by workflow id. It preserves `nodeCount`, copies the v2
timestamps, popularity, and expanded creator metadata, then rebuilds the index.
Descriptions are included in full-text search when present.

`enrich-default-node-compatibility` compares each workflow's node types with
`collection/n8n-nodes.json`. It adds default-catalog compatibility, missing node
types, and package summaries to the map, then rebuilds the index:

```bash
uv run n8n-search search --default-compatible
uv run n8n-search search --no-default-compatible --min-missing-node-types 2
```

## Browser interface

Start the local interface with:

```bash
uv run n8n-search serve
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The interface uses the
same local index as the command-line tool. It does not upload the collection;
press `Ctrl+C` in the terminal to stop it. Use `--port 9000` or `--host 0.0.0.0`
only when you specifically need another address.

## Search examples

```bash
# Terms must all occur (the default)
uv run n8n-search search "google sheets"

# Match either term and rank by popularity
uv run n8n-search search "slack discord" --mode any --sort views

# Filter the metadata index
uv run n8n-search search "automation" --category Marketing --min-views 1000
uv run n8n-search search "postgres" --creator jan

# Print machine-readable results
uv run n8n-search search "notion" --json
uv run n8n-search categories

# Inspect index and map information
uv run n8n-search stats
```

Each result includes the workflow id, categories, creator, view count, n8n gallery URL,
and the local JSON file path. The `--file` and `--index` options allow an alternate map or
index location when needed.

## Git scope

`collection/`, `.n8n-search/`, and local Python environments are ignored. Commit the
search tool and its lockfile, but not the downloaded workflow collection or generated
SQLite index.

## GitHub Pages

`pages/` contains a standalone public search site. It does not publish workflow JSON
files or local file paths. Regenerate its small public search index after updating the
local map, then commit the `pages/` directory:

```bash
uv run n8n-search export-pages
```

The included GitHub Actions workflow deploys `pages/` to GitHub Pages on pushes to
`main`. Enable **Settings → Pages → Source: GitHub Actions** once in the repository.
