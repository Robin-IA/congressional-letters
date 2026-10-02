#!/usr/bin/env python3
"""Serve site/ as static files on 127.0.0.1:$PORT.

A CONTINGENCY, not the preferred route. This explorer is static, so the right
answer is for Caddy to serve ``site/`` from disk and for there to be no
process at all. That is pending confirmation of whether the studio's
``service`` tool has a static type — community-meetings is registered as
"port 8344, proxy type", which implies types exist, but the tool itself is not
in any repository I could read.

If there is no static type, register this the way community-meetings is
registered and Caddy can proxy to it instead.

Deliberately stdlib-only: no venv, no requirements, nothing to install. It
sets the two headers that matter for this corpus and otherwise gets out of the
way.
"""

from __future__ import annotations

import functools
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "site"
PORT = int(os.environ.get("PORT", "8352"))
HOST = os.environ.get("HOST", "127.0.0.1")


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        # The thumbnails are content-addressed by capture digest, so a given
        # URL's bytes never change and it can be cached hard. The data files
        # are rewritten by every build, so they must not be.
        if path.startswith("/thumbs/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif path.endswith((".json", ".html", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # launchd captures stdout; one line per request is enough.
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    if not (ROOT / "index.html").exists():
        raise SystemExit(f"no site to serve at {ROOT}")
    handler = functools.partial(Handler, directory=str(ROOT))
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"serving {ROOT} on http://{HOST}:{PORT}", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
