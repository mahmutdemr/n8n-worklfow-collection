# n8n workflow search

This repository provides a local, fast search interface for the workflow archive in
`collection/`. The archive itself and the generated index are intentionally ignored by Git.

## Quick start

```bash
uv sync
uv run n8n-search build
uv run n8n-search search "postgres slack"
```

The `build` command reads `collection/workflow-map.json` and creates
`.n8n-search/workflows.sqlite3`. It only indexes map metadata, so building is quick even
though the archive contains more than 10,000 workflow JSON files. Re-run it whenever the
map is updated.

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
