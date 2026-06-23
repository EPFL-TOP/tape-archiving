"""Local HTTP server for tape-archive catalogs.

Run ``tape-archive serve <catalog-root>`` to host the master index and all
per-collection catalogs over HTTP. The pages detect HTTP and use a
``/api/save-notes`` endpoint for direct ``notes.json`` writes (no file picker,
no download dance). After every save, the affected collection's catalog HTML
is re-rendered so subsequent reloads — even via ``file://`` — reflect the
new notes.
"""
from __future__ import annotations

import json
import logging
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Lock

log = logging.getLogger("tape_archive.serve")

_lock = Lock()


class CatalogHandler(SimpleHTTPRequestHandler):
    catalog_root: Path | None = None

    def log_message(self, fmt, *args):  # noqa: A003 (stdlib name)
        log.info("%s - %s", self.address_string(), fmt % args)

    def end_headers(self):
        # Disable caching so JS-side fetches of notes.json/shipped.json always
        # see the latest disk content.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):  # noqa: N802 (stdlib name)
        if self.path == "/api/save-notes":
            return self._handle_save_notes()
        self._send_error_json(404, "unknown endpoint")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, msg: str) -> None:
        self._send_json(status, {"ok": False, "error": msg})

    def _handle_save_notes(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            self._send_error_json(400, f"bad body: {e}")
            return

        rel = (data.get("collection_path") or "").strip("/").strip("\\")
        notes = data.get("notes")
        if not isinstance(notes, dict):
            self._send_error_json(400, "`notes` must be an object")
            return
        # Reject path traversal.
        if ".." in rel.replace("\\", "/").split("/"):
            self._send_error_json(400, "invalid path (`..` not allowed)")
            return

        root = type(self).catalog_root.resolve()  # type: ignore[union-attr]
        coll_dir = (root / rel).resolve() if rel else root
        try:
            coll_dir.relative_to(root)
        except ValueError:
            self._send_error_json(400, "path outside catalog root")
            return
        if not coll_dir.is_dir():
            self._send_error_json(404, f"collection not found: {rel!r}")
            return

        notes_path = coll_dir / "notes.json"
        with _lock:
            try:
                notes_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")
                log.info("wrote %s", notes_path)
            except OSError as e:
                self._send_error_json(500, f"write failed: {e}")
                return
            # Re-render this collection's catalog so reloads via file:// also
            # show the new notes. Depth-aware back-link for nested collections.
            try:
                from .catalog_html import render_catalog
                rel_under_root = coll_dir.relative_to(root)
                depth = max(1, len(rel_under_root.parts))
                index_url = "/".join([".."] * depth) + "/index.html"
                render_catalog(coll_dir, coll_dir / "catalog.html", index_url=index_url)
                log.info("re-rendered %s/catalog.html", coll_dir)
            except FileNotFoundError:
                log.debug("no manifests under %s; skipping catalog re-render", coll_dir)
            except Exception as e:
                log.warning("catalog re-render failed: %s", e)

        self._send_json(200, {"ok": True, "path": str(notes_path)})


def serve(catalog_root, host: str = "127.0.0.1", port: int = 8080) -> None:
    root = Path(catalog_root).resolve()
    if not root.is_dir():
        raise SystemExit(f"catalog root not found: {root}")
    CatalogHandler.catalog_root = root
    # SimpleHTTPRequestHandler serves the CWD.
    os.chdir(root)
    httpd = HTTPServer((host, port), CatalogHandler)
    url = f"http://{host}:{port}"
    log.info("serving %s at %s", root, url)
    log.info("open %s/index.html in your browser — Ctrl-C to stop", url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        httpd.server_close()
