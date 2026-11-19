from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from retrieval.db import RetrievalDB
from retrieval.embed import EmbedClient
from retrieval.engine import SearchEngine
from retrieval.inbox import CourseInboxStore
from retrieval.learning_units import LearningUnitCourse, discover_learning_unit_courses
from retrieval.progress import ProgressStore

HOST = os.environ.get("KASCENDO_SEARCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("KASCENDO_SEARCH_PORT", "8093"))
DB_PATH = Path(os.environ.get("KASCENDO_DB", "retrieval_v04.db")).resolve()
EMBED_URL = os.environ.get("KASCENDO_EMBED_URL", "http://127.0.0.1:8091/v1/embeddings")
COURSE_DIR = Path(os.environ.get("KASCENDO_COURSE_DIR", "courses")).resolve()
STATE_DB = Path(os.environ.get("KASCENDO_STATE_DB", "data/state/kascendo_learning.db")).resolve()
INBOX_DB = Path(os.environ.get("KASCENDO_INBOX_DB", "data/state/kascendo_inbox.db")).resolve()
INBOX_ACQUIRED_ROOT = Path(os.environ.get("KASCENDO_INBOX_ACQUIRED_ROOT", "data/inbox/acquired")).resolve()


def embedding_health() -> dict[str, Any]:
    url = EMBED_URL.rsplit("/v1/embeddings", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return {"ok": response.status == 200, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def db_status() -> dict[str, Any]:
    db = RetrievalDB(DB_PATH)
    try:
        return db.status()
    finally:
        db.close()


def learning_unit_courses() -> list[LearningUnitCourse]:
    return discover_learning_unit_courses(COURSE_DIR)


def combined_courses() -> list[dict[str, Any]]:
    db = RetrievalDB(DB_PATH)
    try:
        indexed = db.list_courses()
    finally:
        db.close()
    extra = [course.public_course() for course in learning_unit_courses()]
    known = {str(course["course_id"]) for course in indexed}
    return indexed + [course for course in extra if str(course["course_id"]) not in known]


def learning_unit_course(course_id: str) -> LearningUnitCourse:
    wanted = str(course_id).strip()
    if not wanted or len(wanted) > 200:
        raise ValueError("course_id is required and must be <= 200 characters")
    for course in learning_unit_courses():
        if course.course_id == wanted:
            return course
    raise KeyError(f"Learning Unit course not found: {wanted}")

def state_status() -> dict[str, Any]:
    store = ProgressStore(STATE_DB)
    try:
        return store.status()
    finally:
        store.close()


def inbox_status() -> dict[str, Any]:
    store = CourseInboxStore(INBOX_DB)
    try:
        return store.status(learning_unit_courses())
    finally:
        store.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "KascendoRetrieval/0.4"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            raise ValueError("invalid request body length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=20)
            if path == "/health":
                db = db_status()
                emb = embedding_health()
                units = [course.public_course() for course in learning_unit_courses()]
                self._json(200 if emb.get("ok") else 503, {
                    "ok": bool(emb.get("ok")),
                    "database": db,
                    "embedding": emb,
                    "learning_unit_courses": units,
                    "learning_state": state_status(),
                    "course_inbox": inbox_status(),
                })
                return
            if path == "/v1/courses":
                self._json(200, {"courses": combined_courses()})
                return
            if path == "/v1/units":
                course_id = (params.get("course_id") or [""])[0]
                active_only = (params.get("active_only") or ["false"])[0].lower() in {"1", "true", "yes"}
                course = learning_unit_course(course_id)
                units = course.units(active_only=active_only)
                self._json(200, {
                    "course": course.public_course(),
                    "units": units,
                    "count": len(units),
                })
                return
            if path == "/v1/unit":
                course_id = (params.get("course_id") or [""])[0]
                unit_id = (params.get("unit_id") or [""])[0]
                if not unit_id or len(unit_id) > 200:
                    raise ValueError("unit_id is required and must be <= 200 characters")
                course = learning_unit_course(course_id)
                self._json(200, {"course_id": course.course_id, "unit": course.unit(unit_id)})
                return
            if path == "/v1/next-unit":
                course = learning_unit_course((params.get("course_id") or [""])[0])
                store = ProgressStore(STATE_DB)
                try:
                    self._json(200, {"course_id": course.course_id, "next": store.next_unit(course)})
                finally:
                    store.close()
                return
            if path == "/v1/progress":
                course = learning_unit_course((params.get("course_id") or [""])[0])
                store = ProgressStore(STATE_DB)
                try:
                    self._json(200, store.progress(course))
                finally:
                    store.close()
                return
            if path == "/v1/inbox":
                status = (params.get("status") or [""])[0]
                limit = int((params.get("limit") or ["100"])[0])
                store = CourseInboxStore(INBOX_DB)
                try:
                    self._json(200, {"items": store.list_items(status=status, limit=limit, courses=learning_unit_courses()), "status": store.status(learning_unit_courses())})
                finally:
                    store.close()
                return
            if path == "/v1/inbox/item":
                item_id = int((params.get("id") or ["0"])[0])
                store = CourseInboxStore(INBOX_DB)
                try:
                    self._json(200, {"item": store.get(item_id, courses=learning_unit_courses())})
                finally:
                    store.close()
                return
            self._json(404, {"error": "not_found"})
        except ValueError as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})
        except KeyError as exc:
            self._json(404, {"error": "not_found", "message": str(exc)})
        except Exception as exc:
            self._json(500, {"error": type(exc).__name__, "message": str(exc)})

    def do_POST(self) -> None:
        try:
            body = self._read_json()
            if self.path == "/v1/inbox/add":
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirm=true is required to add a Course Inbox item")
                store = CourseInboxStore(INBOX_DB)
                try:
                    item = store.add(str(body.get("source_uri") or ""), str(body.get("note") or ""), learning_unit_courses())
                    self._json(200, {"item": item, "status": store.status(learning_unit_courses())})
                finally:
                    store.close()
                return
            if self.path == "/v1/inbox/attach-acquisition":
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirm=true is required to attach a Course Inbox acquisition")
                store = CourseInboxStore(INBOX_DB)
                try:
                    item = store.attach_acquisition(
                        int(body.get("item_id") or 0),
                        str(body.get("manifest_path") or ""),
                        learning_unit_courses(),
                        INBOX_ACQUIRED_ROOT,
                    )
                    self._json(200, {"item": item, "status": store.status(learning_unit_courses())})
                finally:
                    store.close()
                return
            if self.path in {"/v1/unit/start", "/v1/unit/finish"}:
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirm=true is required for learning-state mutation")
                course = learning_unit_course(str(body.get("course_id") or ""))
                unit_id = str(body.get("unit_id") or "").strip()
                store = ProgressStore(STATE_DB)
                try:
                    if self.path == "/v1/unit/start":
                        result = store.start(course, unit_id)
                    else:
                        if not unit_id:
                            raise ValueError("unit_id is required for finish")
                        result = store.finish(course, unit_id, body.get("evidence_result"))
                finally:
                    store.close()
                self._json(200, result)
                return
            if self.path != "/v1/search":
                self._json(404, {"error": "not_found"})
                return
            query = str(body.get("query") or "").strip()
            if not query:
                raise ValueError("query is required")
            max_clips = int(body.get("max_clips", 3))
            if max_clips < 1 or max_clips > 3:
                raise ValueError("max_clips must be between 1 and 3")
            filters = dict(body.get("filters") or {})
            course_id = str(filters.get("course_id") or "").strip()
            unit_course = next((course for course in learning_unit_courses() if course.course_id == course_id), None) if course_id else None
            if unit_course is not None:
                unsupported = sorted(set(filters) - {"course_id", "subject", "course_year", "provider"})
                if unsupported:
                    raise ValueError(f"unsupported Learning Unit filters: {', '.join(unsupported)}")
                result = {
                    "query": query,
                    "filters": filters,
                    "material_kind": "learning_unit",
                    "clips": unit_course.search(query, limit=max_clips),
                    "diagnostics": {
                        "course_id": unit_course.course_id,
                        "retriever": "learning_unit_lexical_v1",
                        "semantic": False,
                        "warnings": [],
                    },
                }
                if body.get("roundtrip"):
                    result["roundtrip"] = [
                        {"ok": True, "unit_id": clip["unit_id"], "locator": clip["locator"]}
                        for clip in result["clips"]
                    ]
            else:
                eng = SearchEngine(DB_PATH, EmbedClient(EMBED_URL))
                try:
                    result = eng.search(query, max_clips=max_clips, filters=filters, debug=bool(body.get("debug", False)))
                    if body.get("roundtrip"):
                        result["roundtrip"] = [eng.roundtrip_clip(c) for c in result["clips"]]
                finally:
                    eng.close()
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})
        except Exception as exc:
            self._json(500, {"error": type(exc).__name__, "message": str(exc)})


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"database not found: {DB_PATH}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(json.dumps({
        "service": "kascendo-retrieval",
        "host": HOST,
        "port": PORT,
        "db": str(DB_PATH),
        "course_dir": str(COURSE_DIR),
        "state_db": str(STATE_DB),
        "inbox_db": str(INBOX_DB),
        "inbox_acquired_root": str(INBOX_ACQUIRED_ROOT),
    }, ensure_ascii=False), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
