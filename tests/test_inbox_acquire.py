from __future__ import annotations

import fcntl
import json
import tempfile
import unittest
from pathlib import Path

from retrieval.inbox import CourseInboxStore
from retrieval.inbox_acquire import acquire_inbox_item
from retrieval.stepik_acquire import acquire_to_directory


class FakeStepik:
    def __call__(self, path: str, params=None):
        if path == "/courses/123":
            return {"courses": [{"id": 123, "title": "Demo", "summary": "", "sections": [10], "total_units": 1, "update_date": "2026-09-10T00:00:00Z"}]}
        ids = [int(value) for key, value in (params or []) if key == "ids[]"]
        if path == "/sections":
            return {"sections": [{"id": 10, "position": 1, "title": "S1", "description": "", "units": [20]}]}
        if path == "/units":
            return {"units": [{"id": 20, "position": 1, "lesson": 30}] if 20 in ids else []}
        if path == "/lessons":
            return {"lessons": [{"id": 30, "title": "Intro", "steps": [40], "time_to_complete": 300, "canonical_url": None}] if 30 in ids else []}
        if path == "/steps":
            return {"steps": [{"id": 40, "position": 1, "worth": 0, "correct_ratio": None, "block": {"name": "text", "text": "hello"}}] if 40 in ids else []}
        raise AssertionError((path, params))


def fake_acquire(course_id: int, out_dir: str | Path):
    return acquire_to_directory(course_id, out_dir, fetch=FakeStepik())


class InboxAcquisitionWorkerTests(unittest.TestCase):
    def test_success_attaches_staged_snapshot_without_activation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox_db = root / "inbox.db"
            course_dir = root / "courses"; course_dir.mkdir()
            staging = root / "acquired"
            store = CourseInboxStore(inbox_db)
            try:
                item = store.add("https://stepik.org/course/123", "", [])
            finally:
                store.close()
            result = acquire_inbox_item(item["id"], inbox_db=inbox_db, course_dir=course_dir, staging_root=staging, acquire_fn=fake_acquire)
            self.assertEqual(result["item"]["status"], "staged")
            self.assertEqual(result["item"]["staging_summary"]["draft_units"], 1)
            self.assertFalse(result["auto_activation"])
            self.assertTrue(Path(result["item"]["staging_manifest"]).is_file())

    def test_failed_acquisition_does_not_attach_or_change_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox_db = root / "inbox.db"
            course_dir = root / "courses"; course_dir.mkdir()
            staging = root / "acquired"
            store = CourseInboxStore(inbox_db)
            try:
                item = store.add("https://stepik.org/course/123", "", [])
            finally:
                store.close()
            def fail(course_id: int, out_dir: str | Path):
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                (Path(out_dir) / "partial.txt").write_text("partial")
                raise RuntimeError("boom")
            with self.assertRaisesRegex(RuntimeError, "boom"):
                acquire_inbox_item(item["id"], inbox_db=inbox_db, course_dir=course_dir, staging_root=staging, acquire_fn=fail)
            store = CourseInboxStore(inbox_db)
            try:
                current = store.get(item["id"], courses=[])
                self.assertEqual(current["status"], "needs_acquisition")
                self.assertIsNone(current["staging_manifest"])
            finally:
                store.close()

    def test_non_stepik_item_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox_db = root / "inbox.db"
            course_dir = root / "courses"; course_dir.mkdir()
            store = CourseInboxStore(inbox_db)
            try:
                item = store.add("https://example.com/course", "", [])
            finally:
                store.close()
            with self.assertRaisesRegex(ValueError, "only Stepik"):
                acquire_inbox_item(item["id"], inbox_db=inbox_db, course_dir=course_dir, staging_root=root / "acquired", acquire_fn=fake_acquire)

    def test_concurrent_course_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox_db = root / "inbox.db"
            course_dir = root / "courses"; course_dir.mkdir()
            staging = root / "acquired"
            store = CourseInboxStore(inbox_db)
            try:
                item = store.add("https://stepik.org/course/123", "", [])
            finally:
                store.close()
            lock_dir = staging / ".locks"; lock_dir.mkdir(parents=True)
            lock_path = lock_dir / "stepik-123.lock"
            with lock_path.open("a+b") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    acquire_inbox_item(item["id"], inbox_db=inbox_db, course_dir=course_dir, staging_root=staging, acquire_fn=fake_acquire)


if __name__ == "__main__":
    unittest.main()
