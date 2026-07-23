"""Download and catalog local icon assets for every installed n8n node type."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_NODE_CATALOG_PATH = Path("collection/n8n-nodes.json")
DEFAULT_NODE_MAP_PATH = Path("collection/nodes/node-map.json")
DEFAULT_NODE_ICON_DIRECTORY = Path("collection/nodes/icons")
DEFAULT_N8N_BASE_URL = "http://localhost:5678/"
DEFAULT_N8N_CONTAINER = "n8n"
DEFAULT_FONTAWESOME_VERSION = "5.15.4"

_ICON_MIME_EXTENSIONS = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
_SAFE_ICON_NAME = re.compile(r"^[a-z0-9-]+$")
_SAFE_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class NodeIconDownloadSummary:
    node_types: int
    resolved_node_types: int
    url_references: int
    stored_url_assets: int
    n8n_icons: int
    fontawesome_icons: int
    fallback_node_types: int
    files: int
    bytes: int


@dataclass(frozen=True)
class DownloadedAsset:
    content: bytes
    mime_type: str
    extension: str
    sha256: str


def _load_catalog(catalog_path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    try:
        catalog_bytes = catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Node catalog was not found: {catalog_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Node catalog is not valid JSON: {catalog_path} ({error})") from error
    if not isinstance(catalog, list):
        raise ValueError("Node catalog must be a JSON list.")
    for position, definition in enumerate(catalog):
        if not isinstance(definition, dict) or not definition.get("name"):
            raise ValueError(f"Node catalog record {position} has no node name.")
    return catalog_bytes, catalog


def _write_json_atomic(payload: dict[str, Any], output_path: Path, *, compact: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f".{output_path.name}.", suffix=".tmp",
        dir=output_path.parent, delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        json.dump(
            payload,
            temporary,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        )
        temporary.write("\n")
    try:
        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _mime_type(header_value: str | None, url: str) -> str:
    value = (header_value or "").split(";", 1)[0].strip().lower()
    if value in _ICON_MIME_EXTENSIONS:
        return value
    guessed, _ = mimetypes.guess_type(urllib.parse.urlparse(url).path)
    if guessed in _ICON_MIME_EXTENSIONS:
        return str(guessed)
    raise ValueError(f"Icon response has an unsupported content type: {url} ({header_value or 'unknown'})")


def _validate_icon(content: bytes, mime_type: str, source: str) -> None:
    if not content:
        raise ValueError(f"Icon response is empty: {source}")
    if mime_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Icon response is not a valid PNG: {source}")
    if mime_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"Icon response is not a valid JPEG: {source}")
    if mime_type == "image/svg+xml":
        lowered = content[:4096].lower()
        if b"<svg" not in lowered:
            raise ValueError(f"Icon response is not a valid SVG: {source}")
        if b"<script" in content.lower():
            raise ValueError(f"Icon SVG contains a script element: {source}")


def _download(url: str) -> DownloadedAsset:
    request = urllib.request.Request(url, headers={"User-Agent": "n8n-workflow-collection-icon-downloader/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise ValueError(f"Icon request failed: {url} (HTTP {response.status})")
            content = response.read()
            mime_type = _mime_type(response.headers.get("Content-Type"), url)
    except urllib.error.URLError as error:
        raise ValueError(f"Icon request failed: {url} ({error})") from error
    _validate_icon(content, mime_type, url)
    return DownloadedAsset(
        content=content,
        mime_type=mime_type,
        extension=_ICON_MIME_EXTENSIONS[mime_type],
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _docker_node_icon_directory(container: str) -> str:
    if not _SAFE_CONTAINER_NAME.fullmatch(container):
        raise ValueError("Docker container name contains unsupported characters.")
    command = (
        "find /usr/local/lib/node_modules/n8n/node_modules/.pnpm -type d "
        "-path '*/@n8n/design-system/src/components/N8nIcon/nodes' -print -quit"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", container, "sh", "-c", command],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as error:
        raise FileNotFoundError("Docker was not found. Install Docker or make it available on PATH.") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or "docker exec failed"
        raise ValueError(f"Could not inspect the n8n container '{container}': {message}") from error
    directory = result.stdout.strip()
    if not directory:
        raise FileNotFoundError(f"The n8n design-system node icon directory was not found in container '{container}'.")
    return directory


def _docker_read(container: str, path: str) -> bytes:
    try:
        result = subprocess.run(["docker", "exec", container, "cat", path], check=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode(errors="replace").strip() or "docker exec failed"
        raise FileNotFoundError(f"Could not read n8n icon from container: {path} ({message})") from error
    return result.stdout


def _docker_n8n_version(container: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "exec", container, "n8n", "--version"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _resolved_url(reference: str, n8n_base_url: str) -> str:
    if reference.startswith(("http://", "https://")):
        return reference
    return urllib.parse.urljoin(n8n_base_url.rstrip("/") + "/", reference.lstrip("/"))


def _reference_variants(definition: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    icon_url = definition.get("iconUrl")
    if isinstance(icon_url, str) and icon_url:
        return "iconUrl", {"light": icon_url, "dark": icon_url}
    if isinstance(icon_url, dict):
        light = icon_url.get("light") if isinstance(icon_url.get("light"), str) else ""
        dark = icon_url.get("dark") if isinstance(icon_url.get("dark"), str) else ""
        if light or dark:
            return "iconUrl", {"light": light or dark, "dark": dark or light}
    icon = definition.get("icon")
    if isinstance(icon, str) and icon.startswith("node:"):
        return "n8n-design-system", {"light": icon, "dark": icon}
    if isinstance(icon, str) and icon.startswith("fa:"):
        return "fontawesome", {"light": icon, "dark": icon}
    return None


def merge_icon_manifest(node_map: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    """Attach generated local icon metadata to an in-memory node map."""
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Node icon manifest was not found: {manifest_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Node icon manifest is not valid JSON: {manifest_path} ({error})") from error
    nodes = node_map.get("nodes") if isinstance(node_map, dict) else None
    icon_nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    if not isinstance(nodes, list) or not isinstance(icon_nodes, dict):
        raise ValueError("Node map or icon manifest has an invalid structure.")
    map_types = {str(node.get("type")) for node in nodes if isinstance(node, dict) and node.get("type")}
    if map_types != set(icon_nodes):
        raise ValueError(
            "Node icon manifest does not match the node map "
            f"({len(map_types - set(icon_nodes))} missing, {len(set(icon_nodes) - map_types)} extra)."
        )
    for node in nodes:
        node["icon"] = icon_nodes[str(node["type"])]
    node_map["schemaVersion"] = max(int(node_map.get("schemaVersion", 1)), 2)
    node_map["nodeIconCatalog"] = {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "generatedAt": manifest.get("generatedAt"),
        "summary": manifest.get("summary") or {},
    }
    return node_map


def attach_icon_manifest(node_map_path: Path, manifest_path: Path) -> None:
    try:
        with node_map_path.open(encoding="utf-8") as source:
            node_map = json.load(source)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Node map was not found: {node_map_path}. Run 'n8n-search build-node-map' first.") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Node map is not valid JSON: {node_map_path} ({error})") from error
    _write_json_atomic(merge_icon_manifest(node_map, manifest_path), node_map_path)


def download_node_icons(
    catalog_path: Path = DEFAULT_NODE_CATALOG_PATH,
    node_map_path: Path = DEFAULT_NODE_MAP_PATH,
    output_directory: Path = DEFAULT_NODE_ICON_DIRECTORY,
    *,
    n8n_base_url: str = DEFAULT_N8N_BASE_URL,
    docker_container: str = DEFAULT_N8N_CONTAINER,
    fontawesome_version: str = DEFAULT_FONTAWESOME_VERSION,
) -> NodeIconDownloadSummary:
    """Download every catalog icon, write a manifest, and enrich the node map."""
    catalog_bytes, catalog = _load_catalog(catalog_path)
    definitions_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    url_references: set[str] = set()
    n8n_icon_names: set[str] = set()
    fontawesome_icon_names: set[str] = set()
    for definition in catalog:
        definitions_by_type[str(definition["name"])].append(definition)
        icon_url = definition.get("iconUrl")
        values = icon_url.values() if isinstance(icon_url, dict) else [icon_url] if isinstance(icon_url, str) else []
        url_references.update(str(value) for value in values if isinstance(value, str) and value)
        icon = definition.get("icon")
        if isinstance(icon, str) and icon.startswith("node:"):
            n8n_icon_names.add(icon.removeprefix("node:"))
        elif isinstance(icon, str) and icon.startswith("fa:"):
            fontawesome_icon_names.add(icon.removeprefix("fa:"))

    for name in n8n_icon_names | fontawesome_icon_names:
        if not _SAFE_ICON_NAME.fullmatch(name):
            raise ValueError(f"Icon reference contains an unsupported name: {name}")

    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".icons.", dir=output_directory.parent))
    assets_directory = staging / "assets"
    n8n_directory = staging / "n8n"
    fontawesome_directory = staging / "fontawesome"
    assets_directory.mkdir()
    n8n_directory.mkdir()
    fontawesome_directory.mkdir()

    file_records: dict[str, dict[str, Any]] = {}
    reference_assets: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    try:
        resolved_urls = {reference: _resolved_url(reference, n8n_base_url) for reference in url_references}
        downloaded_by_reference: dict[str, DownloadedAsset] = {}
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(_download, url): reference for reference, url in resolved_urls.items()}
            for future in as_completed(futures):
                reference = futures[future]
                downloaded_by_reference[reference] = future.result()

        path_by_hash: dict[str, str] = {}
        for reference in sorted(downloaded_by_reference):
            asset = downloaded_by_reference[reference]
            local_path = path_by_hash.get(asset.sha256)
            if local_path is None:
                filename = f"{asset.sha256[:24]}{asset.extension}"
                relative_file = Path("assets") / filename
                (staging / relative_file).write_bytes(asset.content)
                local_path = (Path(output_directory.name) / relative_file).as_posix()
                path_by_hash[asset.sha256] = local_path
                file_records[local_path] = {
                    "path": local_path,
                    "sha256": asset.sha256,
                    "mimeType": asset.mime_type,
                    "bytes": len(asset.content),
                    "sourceType": "iconUrl",
                    "originalReferences": [],
                }
                total_bytes += len(asset.content)
            file_records[local_path]["originalReferences"].append(reference)
            reference_assets[reference] = {
                "path": local_path,
                "sha256": asset.sha256,
                "mimeType": asset.mime_type,
            }

        node_icon_directory = _docker_node_icon_directory(docker_container)
        n8n_assets: dict[str, dict[str, Any]] = {}
        for name in sorted(n8n_icon_names | {"n8n"}):
            source_path = f"{node_icon_directory}/{name}.svg"
            content = _docker_read(docker_container, source_path)
            _validate_icon(content, "image/svg+xml", f"{docker_container}:{source_path}")
            sha256 = hashlib.sha256(content).hexdigest()
            relative_file = Path("n8n") / f"{name}.svg"
            (staging / relative_file).write_bytes(content)
            local_path = (Path(output_directory.name) / relative_file).as_posix()
            record = {
                "path": local_path,
                "sha256": sha256,
                "mimeType": "image/svg+xml",
                "bytes": len(content),
                "sourceType": "n8n-design-system",
                "originalReferences": [f"node:{name}"],
            }
            file_records[local_path] = record
            n8n_assets[name] = {key: record[key] for key in ("path", "sha256", "mimeType")}
            total_bytes += len(content)

        fallback_content = (staging / "n8n" / "n8n.svg").read_bytes()
        (staging / "fallback.svg").write_bytes(fallback_content)
        fallback_sha256 = hashlib.sha256(fallback_content).hexdigest()
        fallback_path = (Path(output_directory.name) / "fallback.svg").as_posix()
        file_records[fallback_path] = {
            "path": fallback_path,
            "sha256": fallback_sha256,
            "mimeType": "image/svg+xml",
            "bytes": len(fallback_content),
            "sourceType": "fallback",
            "originalReferences": ["node:n8n"],
        }
        total_bytes += len(fallback_content)

        fontawesome_assets: dict[str, dict[str, Any]] = {}
        fontawesome_base_url = (
            f"https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@{fontawesome_version}/svgs/solid/"
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_download, f"{fontawesome_base_url}{name}.svg"): name
                for name in fontawesome_icon_names
            }
            downloaded_fontawesome = {futures[future]: future.result() for future in as_completed(futures)}
        for name in sorted(downloaded_fontawesome):
            asset = downloaded_fontawesome[name]
            relative_file = Path("fontawesome") / f"{name}.svg"
            (staging / relative_file).write_bytes(asset.content)
            local_path = (Path(output_directory.name) / relative_file).as_posix()
            record = {
                "path": local_path,
                "sha256": asset.sha256,
                "mimeType": asset.mime_type,
                "bytes": len(asset.content),
                "sourceType": "fontawesome",
                "originalReferences": [f"fa:{name}"],
            }
            file_records[local_path] = record
            fontawesome_assets[name] = {key: record[key] for key in ("path", "sha256", "mimeType")}
            total_bytes += len(asset.content)

        license_url = (
            f"https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@{fontawesome_version}/LICENSE.txt"
        )
        with urllib.request.urlopen(
            urllib.request.Request(license_url, headers={"User-Agent": "n8n-workflow-collection-icon-downloader/1.0"}),
            timeout=20,
        ) as response:
            license_content = response.read()
        (fontawesome_directory / "LICENSE.txt").write_bytes(license_content)
        total_bytes += len(license_content)

        node_records: dict[str, dict[str, Any]] = {}
        fallback_node_types = 0
        for node_type in sorted(definitions_by_type):
            selected = next(
                (reference for definition in definitions_by_type[node_type] if (reference := _reference_variants(definition))),
                None,
            )
            if selected is None:
                fallback_node_types += 1
                variants = {"light": fallback_path, "dark": fallback_path}
                hashes = {"light": fallback_sha256, "dark": fallback_sha256}
                node_records[node_type] = {
                    "light": variants["light"],
                    "dark": variants["dark"],
                    "source": "fallback",
                    "originalReferences": {},
                    "sha256": hashes,
                    "fallback": True,
                }
                continue
            source_type, references = selected
            if source_type == "iconUrl":
                variant_assets = {variant: reference_assets[reference] for variant, reference in references.items()}
            elif source_type == "n8n-design-system":
                variant_assets = {
                    variant: n8n_assets[reference.removeprefix("node:")]
                    for variant, reference in references.items()
                }
            else:
                variant_assets = {
                    variant: fontawesome_assets[reference.removeprefix("fa:")]
                    for variant, reference in references.items()
                }
            node_records[node_type] = {
                "light": variant_assets["light"]["path"],
                "dark": variant_assets["dark"]["path"],
                "source": source_type,
                "originalReferences": references,
                "sha256": {
                    "light": variant_assets["light"]["sha256"],
                    "dark": variant_assets["dark"]["sha256"],
                },
                "fallback": False,
            }

        generated_at = datetime.now(UTC).isoformat()
        summary = {
            "catalogRecordCount": len(catalog),
            "nodeTypeCount": len(definitions_by_type),
            "resolvedNodeTypeCount": len(node_records),
            "urlReferenceCount": len(url_references),
            "storedUrlAssetCount": len(path_by_hash),
            "n8nIconCount": len(n8n_icon_names),
            "fontAwesomeIconCount": len(fontawesome_icon_names),
            "fallbackNodeTypeCount": fallback_node_types,
            "fileCount": len(file_records) + 1,
            "totalBytes": total_bytes,
        }
        manifest = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "sources": {
                "nodeCatalog": {"path": str(catalog_path), "sha256": hashlib.sha256(catalog_bytes).hexdigest()},
                "n8n": {
                    "baseUrl": n8n_base_url.rstrip("/") + "/",
                    "dockerContainer": docker_container,
                    "version": _docker_n8n_version(docker_container),
                },
                "fontAwesome": {
                    "package": "@fortawesome/fontawesome-free",
                    "version": fontawesome_version,
                    "license": "CC BY 4.0 (icons)",
                    "licensePath": f"{output_directory.name}/fontawesome/LICENSE.txt",
                },
            },
            "summary": summary,
            "files": [file_records[path] for path in sorted(file_records)],
            "nodes": node_records,
        }
        _write_json_atomic(manifest, staging / "manifest.json")

        backup = output_directory.parent / f".{output_directory.name}.backup"
        if backup.exists():
            shutil.rmtree(backup)
        if output_directory.exists():
            output_directory.replace(backup)
        try:
            staging.replace(output_directory)
        except BaseException:
            if backup.exists() and not output_directory.exists():
                backup.replace(output_directory)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)
        attach_icon_manifest(node_map_path, output_directory / "manifest.json")
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return NodeIconDownloadSummary(
        node_types=len(definitions_by_type),
        resolved_node_types=len(node_records),
        url_references=len(url_references),
        stored_url_assets=len(path_by_hash),
        n8n_icons=len(n8n_icon_names),
        fontawesome_icons=len(fontawesome_icon_names),
        fallback_node_types=fallback_node_types,
        files=len(file_records) + 1,
        bytes=total_bytes,
    )
