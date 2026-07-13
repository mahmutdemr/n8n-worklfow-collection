"""Local HTTP server for the workflow search interface."""

from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Type
from urllib.parse import parse_qs, urlparse

from .search import DEFAULT_INDEX_PATH, DEFAULT_MAP_PATH, get_stats, resolved_local_file, search

STATIC_DIR = Path(__file__).parent / "static"


def _one(parameters: dict[str, list[str]], name: str, default: str = "") -> str:
    return parameters.get(name, [default])[0]


def _integer(value: str, name: str, default: int | None = None) -> int | None:
    if not value:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number.") from error


def create_handler(index_path: Path, map_path: Path) -> Type[SimpleHTTPRequestHandler]:
    """Create a request handler bound to one local index and collection map."""

    class WorkflowSearchHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            request = urlparse(self.path)
            if request.path == "/api/stats":
                self._send_json(HTTPStatus.OK, get_stats(index_path))
                return
            if request.path == "/api/search":
                self._handle_search(parse_qs(request.query))
                return
            super().do_GET()

        def _handle_search(self, parameters: dict[str, list[str]]) -> None:
            query = _one(parameters, "q").strip()
            if not query:
                self._send_json(HTTPStatus.OK, {"results": []})
                return
            try:
                results = search(
                    query,
                    index_path=index_path,
                    mode=_one(parameters, "mode", "all"),
                    category=_one(parameters, "category").strip() or None,
                    creator=_one(parameters, "creator").strip() or None,
                    min_views=_integer(_one(parameters, "min_views"), "Minimum views"),
                    limit=_integer(_one(parameters, "limit"), "Limit", 30) or 30,
                    sort=_one(parameters, "sort", "rank"),
                )
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return

            payload = []
            for result in results:
                item = asdict(result)
                item["local_file"] = str(resolved_local_file(result, map_path))
                payload.append(item)
            self._send_json(HTTPStatus.OK, {"results": payload})

        def _send_json(self, status: HTTPStatus, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            """Keep routine browser requests out of the terminal."""

    return WorkflowSearchHandler


def serve(
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    map_path: Path = DEFAULT_MAP_PATH,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the local browser UI until interrupted."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Search index was not found: {index_path}. Run 'n8n-search build' first.")
    server = ThreadingHTTPServer((host, port), create_handler(index_path, map_path))
    address = f"http://{host}:{server.server_port}"
    print(f"Workflow search is available at {address}")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
