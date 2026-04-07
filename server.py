#!/usr/bin/env python3
"""Token Auditor server — serves dashboard and handles auto-fix POST endpoints."""
import json
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

from fixes import add_to_claude_md

PORT = 8787
ROOT = Path(__file__).parent


class AuditorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # Disable caching so the dashboard always fetches fresh data
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_POST(self):
        if self.path == "/api/fix/add-to-claude-md":
            self._handle_add_to_claude_md()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"unknown endpoint"}')

    def _handle_add_to_claude_md(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            file_path = body.get("file_path")
            if not file_path:
                raise ValueError("file_path is required")

            result = add_to_claude_md.run(Path(file_path))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "claude_md": str(result),
                "message": f"Added Quick Reference to {result}",
            }).encode("utf-8"))
        except Exception as e:
            traceback.print_exc()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))


def main():
    server = HTTPServer(("127.0.0.1", PORT), AuditorHandler)
    print(f"Token Auditor server running at http://127.0.0.1:{PORT}/dashboard.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server")
        server.server_close()


if __name__ == "__main__":
    main()
