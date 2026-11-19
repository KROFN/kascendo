from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from retrieval.inbox import CourseInboxStore, normalize_source
from retrieval.learning_units import LearningUnitCourse
import serve



def write_acquisition(root: Path, course_id: int, *, active: bool = False, evidence_mode: str = "content_progress_only") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    draft = {
        "schema_version": 1,
        "course_id": course_id,
        "skill_id": f"stepik_course_{course_id}",
        "draft": True,
        "units": [{
            "id": f"STEP-{course_id}-U1",
            "version": 1,
            "parent_skill_id": f"stepik_course_{course_id}",
            "title": "Demo",
            "active": active,
            "evidence_mode": evidence_mode,
            "locator": {
                "provider": "stepik",
                "course_id": course_id,
                "lesson_id": 10,
                "step_start": 1,
                "step_end": 1,
                "url": "https://stepik.org/lesson/10/step/1",
            },
        }],
    }
    payloads = {
        f"stepik-course-{course_id}.raw.json": "{}\n",
        f"stepik-course-{course_id}.map.json": "{}\n",
        f"stepik-course-{course_id}.draft-units.json": json.dumps(draft),
        f"stepik-course-{course_id}.index.md": "# Demo\n",
    }
    files = {}
    for name, content in payloads.items():
        path = root / name
        path.write_text(content, encoding="utf-8")
        files[name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = {
        "schema_version": 1,
        "provider": "stepik",
        "provider_course_id": course_id,
        "source_uri": f"https://stepik.org/course/{course_id}",
        "provider_updated_at": "2026-09-10T00:00:00Z",
        "acquired_at": "2026-09-10T00:00:01+00:00",
        "counts": {"sections": 1, "units": 1, "lessons": 1, "steps": 1, "draft_units": 1},
        "activation_status": "staged_not_active",
        "files": files,
    }
    path = root / f"stepik-course-{course_id}.acquisition.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class ServiceStatusContractTests(unittest.TestCase):
    def test_learning_state_status_does_not_receive_course_catalog(self):
        fake = mock.MagicMock()
        fake.status.return_value = {"unit_progress": 0}
        with mock.patch.object(serve, "ProgressStore", return_value=fake):
            self.assertEqual(serve.state_status(), {"unit_progress": 0})
        fake.status.assert_called_once_with()
        fake.close.assert_called_once_with()

    def test_inbox_status_reconciles_against_course_catalog(self):
        fake = mock.MagicMock()
        fake.status.return_value = {"items": 0}
        courses = [mock.sentinel.course]
        with mock.patch.object(serve, "CourseInboxStore", return_value=fake), \
             mock.patch.object(serve, "learning_unit_courses", return_value=courses):
            self.assertEqual(serve.inbox_status(), {"items": 0})
        fake.status.assert_called_once_with(courses)
        fake.close.assert_called_once_with()


class CourseInboxTests(unittest.TestCase):
    def test_stepik_normalization_is_canonical_and_strips_suffix(self):
        item = normalize_source("https://www.stepik.org/course/58852/syllabus?unit=1#x")
        self.assertEqual(item["source_key"], "stepik:58852")
        self.assertEqual(item["source_kind"], "stepik_course")
        self.assertEqual(item["source_uri"], "https://stepik.org/course/58852")
        self.assertEqual(item["provider_course_id"], 58852)

    def test_manual_url_is_https_only_and_credentials_are_rejected(self):
        item = normalize_source("https://Example.com/course?a=1#fragment")
        self.assertEqual(item["source_kind"], "manual_url")
        self.assertEqual(item["source_uri"], "https://example.com/course?a=1")
        with self.assertRaises(ValueError):
            normalize_source("http://example.com/course")
        with self.assertRaises(ValueError):
            normalize_source("https://user:pass@example.com/course")

    def test_known_stepik_is_already_imported_and_duplicate_add_is_idempotent(self):
        root = Path(__file__).resolve().parents[1]
        known = LearningUnitCourse.load(root / "courses" / "stepik-python-beginner-58852.learning-units.json")
        with tempfile.TemporaryDirectory() as td:
            store = CourseInboxStore(Path(td) / "inbox.db")
            try:
                first = store.add("https://stepik.org/course/58852", "known", [known])
                self.assertEqual(first["status"], "already_imported")
                self.assertEqual(first["linked_course_id"], known.course_id)
                self.assertFalse(first["already_present"])
                second = store.add("https://www.stepik.org/course/58852/syllabus", "ignored duplicate note", [known])
                self.assertEqual(second["id"], first["id"])
                self.assertTrue(second["already_present"])
                self.assertEqual(store.status()["items"], 1)
            finally:
                store.close()

    def test_stepik_status_reconciles_after_course_becomes_known(self):
        root = Path(__file__).resolve().parents[1]
        known = LearningUnitCourse.load(root / "courses" / "stepik-python-beginner-58852.learning-units.json")
        with tempfile.TemporaryDirectory() as td:
            store = CourseInboxStore(Path(td) / "inbox.db")
            try:
                first = store.add("https://stepik.org/course/58852", "", [])
                self.assertEqual(first["status"], "needs_acquisition")
                refreshed = store.get(first["id"], courses=[known])
                self.assertEqual(refreshed["status"], "already_imported")
                self.assertEqual(refreshed["linked_course_id"], known.course_id)
            finally:
                store.close()

    def test_old_inbox_schema_migrates_forward_without_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "inbox.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """CREATE TABLE course_inbox(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE,
                   source_kind TEXT NOT NULL, source_uri TEXT NOT NULL, provider_course_id INTEGER,
                   note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, linked_course_id TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"""
            )
            conn.execute(
                "INSERT INTO course_inbox(source_key,source_kind,source_uri,provider_course_id,note,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ("stepik:1", "stepik_course", "https://stepik.org/course/1", 1, "", "needs_acquisition", "x", "x"),
            )
            conn.commit(); conn.close()
            store = CourseInboxStore(db)
            try:
                columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(course_inbox)")}
                self.assertIn("staging_manifest", columns)
                self.assertIn("staging_summary_json", columns)
                self.assertEqual(store.get(1)["source_key"], "stepik:1")
            finally:
                store.close()

    def test_attach_acquisition_stages_new_course_and_rejects_unsafe_draft(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staging = root / "acquired"
            manifest = write_acquisition(staging / "new", 99999999)
            store = CourseInboxStore(root / "inbox.db")
            try:
                item = store.add("https://stepik.org/course/99999999", "", [])
                attached = store.attach_acquisition(item["id"], manifest, [], staging)
                self.assertEqual(attached["status"], "staged")
                self.assertEqual(attached["staging_summary"]["draft_units"], 1)
                self.assertTrue(str(attached["staging_manifest"]).startswith(str(staging.resolve())))
                bad = write_acquisition(staging / "bad", 99999998, active=True)
                bad_item = store.add("https://stepik.org/course/99999998", "", [])
                with self.assertRaises(ValueError):
                    store.attach_acquisition(bad_item["id"], bad, [], staging)
            finally:
                store.close()

    def test_attach_existing_course_becomes_update_staged_and_path_escape_is_blocked(self):
        root = Path(__file__).resolve().parents[1]
        known = LearningUnitCourse.load(root / "courses" / "stepik-python-beginner-58852.learning-units.json")
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            staging = temp / "acquired"
            manifest = write_acquisition(staging / "known", 58852)
            outside = write_acquisition(temp / "outside", 58852)
            store = CourseInboxStore(temp / "inbox.db")
            try:
                item = store.add("https://stepik.org/course/58852", "", [known])
                attached = store.attach_acquisition(item["id"], manifest, [known], staging)
                self.assertEqual(attached["status"], "update_staged")
                self.assertEqual(attached["linked_course_id"], known.course_id)
                with self.assertRaises(ValueError):
                    store.attach_acquisition(item["id"], outside, [known], staging)
            finally:
                store.close()

    def test_unknown_stepik_needs_acquisition_and_manual_url_needs_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            store = CourseInboxStore(Path(td) / "inbox.db")
            try:
                stepik = store.add("https://stepik.org/course/99999999", "", [])
                manual = store.add("https://example.com/material", "", [])
                self.assertEqual(stepik["status"], "needs_acquisition")
                self.assertEqual(manual["status"], "needs_adapter")
                self.assertEqual(len(store.list_items()), 2)
                self.assertEqual(store.get(stepik["id"])["source_key"], "stepik:99999999")
                self.assertEqual(Path(store.path).stat().st_mode & 0o777, 0o600)
                self.assertFalse(store.status()["network_fetches"])
                self.assertFalse(store.status()["auto_activation"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
