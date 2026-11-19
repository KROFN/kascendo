"""Real localhost HTTP smoke test with a synthetic index and mock embed API."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import serve
from retrieval.adapter import ASRAdapter
from retrieval.chunker import TimestampChunker
from retrieval.engine import SearchEngine


class StubEmbedder:
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]


class MockEmbeddingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        rows = [{"index": n, "embedding": [1.0] + [0.0] * 1023}
                for n, _ in enumerate(request["input"])]
        payload = json.dumps({"data": rows}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class HttpApiSmokeTests(unittest.TestCase):
    def test_real_api_returns_source_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "demo.json"
            transcript.write_text(json.dumps({
                "schema_version": 2, "status": "complete",
                "source": {
                    "source_id": "http_demo", "title": "Тест арифметики",
                    "subject": "math", "provider": "synthetic",
                    "course_year": "demo", "sequence": 1,
                    "duration_seconds": 8,
                    "source_urls": ["https://example.org/lesson/1"],
                },
                "segments": [
                    {"id": 1, "start": 0, "end": 4, "text": "Урок про остаток при делении."},
                    {"id": 2, "start": 4, "end": 8, "text": "Проверяем остаток обычным делением."},
                ],
            }, ensure_ascii=False))
            db = root / "test.db"
            engine = SearchEngine(db, StubEmbedder())
            try:
                engine.db.init_schema()
                doc = ASRAdapter.load(transcript, corpus="public_demo")
                chunks = TimestampChunker(target_tokens=7, overlap_segments=0).chunk(doc)
                engine.db.index_document(doc, chunks, StubEmbedder())
            finally:
                engine.close()

            embed_server = ThreadingHTTPServer(("127.0.0.1", 0), MockEmbeddingHandler)
            api_server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
            threads = [
                threading.Thread(target=s.serve_forever, daemon=True)
                for s in (embed_server, api_server)
            ]
            for t in threads:
                t.start()
            embed_url = f"http://127.0.0.1:{embed_server.server_port}/v1/embeddings"
            api = f"http://127.0.0.1:{api_server.server_port}"
            try:
                with patch.multiple(
                    serve, DB_PATH=db, EMBED_URL=embed_url,
                    COURSE_DIR=root / "courses",
                    INBOX_DB=root / "inbox.db",
                    STATE_DB=root / "learning.db",
                ):
                    with urllib.request.urlopen(api + "/v1/courses", timeout=5) as resp:
                        courses = json.load(resp)["courses"]
                        self.assertTrue(courses)

                    req = urllib.request.Request(
                        api + "/v1/search",
                        method="POST",
                        headers={"Content-Type": "application/json"},
                        data=json.dumps({
                            "query": "остаток при делении",
                            "max_clips": 2,
                            "roundtrip": True,
                        }).encode("utf-8"),
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        result = json.load(resp)
                        self.assertTrue(result["clips"])
                        self.assertEqual(
                            result["clips"][0]["source_url"],
                            "https://example.org/lesson/1",
                        )
                        self.assertTrue(all(item["ok"] for item in result["roundtrip"]))
            finally:
                for server in (api_server, embed_server):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
