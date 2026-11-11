"""Temporary authenticated TLS receiver for public model assets, confined to one model directory."""

import hashlib
import hmac
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data/private"
KEY = (PRIVATE / "gateway.key").read_text().strip()
ALLOWED = {"Qwen3-Embedding-8B", "Qwen3-Reranker-0.6B", "Qwen3-4B"}


class Receiver(BaseHTTPRequestHandler):
    def do_PUT(self):
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + KEY):
            self.send_error(401)
            return
        relative = Path(unquote(self.path.lstrip("/")))
        if not relative.parts or relative.parts[0] not in ALLOWED or ".." in relative.parts:
            self.send_error(400)
            return
        if relative.suffix not in (".json", ".safetensors", ".txt", ".jinja", ".md"):
            self.send_error(400)
            return
        target = (ROOT / "models" / relative).resolve()
        if not target.is_relative_to(ROOT / "models"):
            self.send_error(400)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size < 8 * 1024**3:
            self.send_error(413)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".incoming")
        digest = hashlib.sha256()
        remaining = size
        with temporary.open("wb") as output:
            while remaining:
                data = self.rfile.read(min(1024 * 1024, remaining))
                if not data:
                    raise ConnectionError("Incomplete upload")
                output.write(data)
                digest.update(data)
                remaining -= len(data)
        if digest.hexdigest() != self.headers.get("X-Content-SHA256"):
            self.send_error(422)
            return
        temporary.replace(target)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"stored"}')


server = ThreadingHTTPServer((os.environ.get("TARIFF_ASSET_RECEIVER_HOST", "127.0.0.1"), 18082), Receiver)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(PRIVATE / "server.crt", PRIVATE / "server.key")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
