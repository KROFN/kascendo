from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .learning_units import LearningUnitCourse

_ALLOWED_STATUSES = frozenset({"already_imported", "needs_acquisition", "needs_adapter", "staged", "update_staged"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_source(source_uri: str) -> dict[str, Any]:
    raw = str(source_uri or "").strip()
    if not raw or len(raw) > 4000:
        raise ValueError("source_uri is required and must be <= 4000 characters")
    parsed = urlsplit(raw)
    if parsed.username or parsed.password:
        raise ValueError("source_uri must not contain credentials")
    host = (parsed.hostname or "").lower()
    if host in {"stepik.org", "www.stepik.org"}:
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Stepik source_uri must use http/https")
        parts = [part for part in parsed.path.split("/") if part]
        try:
            idx = parts.index("course")
            provider_course_id = int(parts[idx + 1])
        except (ValueError, IndexError) as exc:
            raise ValueError("Stepik source_uri must contain /course/<id>") from exc
        if provider_course_id < 1:
            raise ValueError("Stepik course id must be positive")
        canonical = f"https://stepik.org/course/{provider_course_id}"
        return {
            "source_key": f"stepik:{provider_course_id}",
            "source_kind": "stepik_course",
            "source_uri": canonical,
            "provider_course_id": provider_course_id,
        }
    if parsed.scheme != "https" or not host:
        raise ValueError("manual source_uri must be an absolute https URL")
    canonical = urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    return {
        "source_key": f"url:{canonical}",
        "source_kind": "manual_url",
        "source_uri": canonical,
        "provider_course_id": None,
    }


class CourseInboxStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS course_inbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                source_kind TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                provider_course_id INTEGER,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                linked_course_id TEXT,
                staging_manifest TEXT,
                staging_summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_course_inbox_status ON course_inbox(status,id);
            """
        )
        columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(course_inbox)").fetchall()}
        with self.conn:
            if "staging_manifest" not in columns:
                self.conn.execute("ALTER TABLE course_inbox ADD COLUMN staging_manifest TEXT")
            if "staging_summary_json" not in columns:
                self.conn.execute("ALTER TABLE course_inbox ADD COLUMN staging_summary_json TEXT")
        self.conn.commit()

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        raw = item.pop("staging_summary_json", None)
        item["staging_summary"] = json.loads(str(raw)) if raw else None
        return item

    @staticmethod
    def _known_stepik(courses: list[LearningUnitCourse]) -> dict[int, str]:
        return {int(course.provider_course_id): course.course_id for course in courses}

    def reconcile(self, courses: list[LearningUnitCourse]) -> None:
        known = self._known_stepik(courses)
        rows = self.conn.execute(
            "SELECT id,provider_course_id,status,linked_course_id,staging_manifest FROM course_inbox WHERE source_kind='stepik_course'"
        ).fetchall()
        now = now_iso()
        with self.conn:
            for row in rows:
                linked = known.get(int(row["provider_course_id"])) if row["provider_course_id"] is not None else None
                if row["staging_manifest"]:
                    status = "update_staged" if linked else "staged"
                else:
                    status = "already_imported" if linked else "needs_acquisition"
                if status != row["status"] or linked != row["linked_course_id"]:
                    self.conn.execute(
                        "UPDATE course_inbox SET status=?,linked_course_id=?,updated_at=? WHERE id=?",
                        (status, linked, now, int(row["id"])),
                    )

    def add(self, source_uri: str, note: str, courses: list[LearningUnitCourse]) -> dict[str, Any]:
        normalized = normalize_source(source_uri)
        note = str(note or "").strip()
        if len(note) > 4000:
            raise ValueError("note must be <= 4000 characters")
        linked_course_id: str | None = None
        if normalized["source_kind"] == "stepik_course":
            linked_course_id = self._known_stepik(courses).get(int(normalized["provider_course_id"]))
            status = "already_imported" if linked_course_id else "needs_acquisition"
        else:
            status = "needs_adapter"
        now = now_iso()
        with self.conn:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO course_inbox(
                       source_key,source_kind,source_uri,provider_course_id,note,status,linked_course_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    normalized["source_key"], normalized["source_kind"], normalized["source_uri"],
                    normalized["provider_course_id"], note, status, linked_course_id, now, now,
                ),
            )
        row = self.conn.execute("SELECT * FROM course_inbox WHERE source_key=?", (normalized["source_key"],)).fetchone()
        if row is None:
            raise RuntimeError("course inbox insert failed")
        result = self._public(row)
        result["already_present"] = cursor.rowcount == 0
        return result

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def attach_acquisition(
        self,
        item_id: int,
        manifest_path: str | Path,
        courses: list[LearningUnitCourse],
        staging_root: str | Path,
    ) -> dict[str, Any]:
        item_id = int(item_id)
        if item_id < 1:
            raise ValueError("item_id must be positive")
        row = self.conn.execute("SELECT * FROM course_inbox WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"Course Inbox item not found: {item_id}")
        if str(row["source_kind"]) != "stepik_course":
            raise ValueError("acquisition attachment is currently supported only for Stepik course items")
        root = Path(staging_root).resolve()
        manifest_file = Path(manifest_path).resolve()
        try:
            manifest_file.relative_to(root)
        except ValueError as exc:
            raise ValueError("acquisition manifest must stay under the configured staging root") from exc
        if not manifest_file.is_file():
            raise FileNotFoundError(f"acquisition manifest not found: {manifest_file}")
        if manifest_file.stat().st_size > 1_000_000:
            raise ValueError("acquisition manifest exceeds 1 MB")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or int(manifest.get("schema_version", 0)) != 1:
            raise ValueError("invalid acquisition manifest schema")
        provider_course_id = int(row["provider_course_id"] or 0)
        if manifest.get("provider") != "stepik" or int(manifest.get("provider_course_id", 0)) != provider_course_id:
            raise ValueError("acquisition manifest provider/course does not match Inbox item")
        if str(manifest.get("source_uri") or "") != str(row["source_uri"]):
            raise ValueError("acquisition manifest source_uri does not match Inbox item")
        if manifest.get("activation_status") != "staged_not_active":
            raise ValueError("acquisition manifest must be staged_not_active")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or len(files) > 10:
            raise ValueError("acquisition manifest files must contain 1..10 entries")
        total_bytes = 0
        draft_files: list[Path] = []
        for name, info in files.items():
            if not isinstance(name, str) or Path(name).name != name or not isinstance(info, dict):
                raise ValueError("acquisition manifest contains an unsafe file entry")
            file_path = (manifest_file.parent / name).resolve()
            try:
                file_path.relative_to(root)
            except ValueError as exc:
                raise ValueError("acquisition file escapes staging root") from exc
            if not file_path.is_file():
                raise FileNotFoundError(f"acquisition file not found: {file_path}")
            size = int(info.get("bytes", -1))
            expected_sha = str(info.get("sha256") or "")
            if size < 0 or size > 100_000_000 or len(expected_sha) != 64:
                raise ValueError(f"invalid acquisition file metadata: {name}")
            actual_size = file_path.stat().st_size
            actual_sha = self._file_sha256(file_path)
            if actual_size != size or actual_sha != expected_sha:
                raise ValueError(f"acquisition file hash/size mismatch: {name}")
            total_bytes += actual_size
            if total_bytes > 500_000_000:
                raise ValueError("acquisition bundle exceeds 500 MB")
            if name.endswith(".draft-units.json"):
                draft_files.append(file_path)
        if len(draft_files) != 1:
            raise ValueError("acquisition must contain exactly one draft-units file")
        draft_file = draft_files[0]
        if draft_file.stat().st_size > 20_000_000:
            raise ValueError("draft-units file exceeds 20 MB")
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
        if not isinstance(draft, dict) or int(draft.get("schema_version", 0)) != 1 or draft.get("draft") is not True:
            raise ValueError("invalid draft-units document")
        if int(draft.get("course_id", 0)) != provider_course_id:
            raise ValueError("draft-units course_id does not match Inbox item")
        units = draft.get("units")
        if not isinstance(units, list) or len(units) > 5000:
            raise ValueError("draft-units units must be a list of at most 5000 entries")
        for unit in units:
            if not isinstance(unit, dict) or unit.get("active") is not False:
                raise ValueError("all staged draft Units must be inactive")
            if unit.get("evidence_mode") != "content_progress_only":
                raise ValueError("staged draft Units must use content_progress_only Evidence mode")
            locator = unit.get("locator") or {}
            if locator.get("provider") != "stepik" or int(locator.get("course_id", 0)) != provider_course_id:
                raise ValueError("staged draft Unit locator does not match Stepik course")
        linked = self._known_stepik(courses).get(provider_course_id)
        status = "update_staged" if linked else "staged"
        summary = {
            "provider_updated_at": manifest.get("provider_updated_at"),
            "acquired_at": manifest.get("acquired_at"),
            "counts": manifest.get("counts"),
            "activation_status": manifest.get("activation_status"),
            "draft_units": len(units),
            "bundle_bytes": total_bytes,
        }
        now = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE course_inbox SET status=?,linked_course_id=?,staging_manifest=?,staging_summary_json=?,updated_at=? WHERE id=?",
                (status, linked, str(manifest_file), json.dumps(summary, ensure_ascii=False, sort_keys=True), now, item_id),
            )
        return self.get(item_id, courses=courses)

    def list_items(self, status: str = "", limit: int = 100, courses: list[LearningUnitCourse] | None = None) -> list[dict[str, Any]]:
        if courses is not None:
            self.reconcile(courses)
        status = str(status or "").strip()
        limit = int(limit)
        if status and status not in _ALLOWED_STATUSES:
            raise ValueError(f"unsupported inbox status: {status}")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if status:
            rows = self.conn.execute(
                "SELECT * FROM course_inbox WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM course_inbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._public(row) for row in rows]

    def get(self, item_id: int, courses: list[LearningUnitCourse] | None = None) -> dict[str, Any]:
        if courses is not None:
            self.reconcile(courses)
        item_id = int(item_id)
        if item_id < 1:
            raise ValueError("item_id must be positive")
        row = self.conn.execute("SELECT * FROM course_inbox WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"Course Inbox item not found: {item_id}")
        return self._public(row)

    def status(self, courses: list[LearningUnitCourse] | None = None) -> dict[str, Any]:
        if courses is not None:
            self.reconcile(courses)
        rows = self.conn.execute("SELECT status,COUNT(*) AS n FROM course_inbox GROUP BY status ORDER BY status").fetchall()
        counts = {str(row["status"]): int(row["n"]) for row in rows}
        return {
            "path": str(self.path),
            "items": sum(counts.values()),
            "counts": counts,
            "network_fetches": False,
            "auto_activation": False,
        }
