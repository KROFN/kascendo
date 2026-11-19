from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from retrieval.course import CourseManifest
from retrieval.db import RetrievalDB


def canonical(path: Path, sequence: int) -> None:
    payload = {
        "schema_version": 2,
        "status": "complete",
        "source": {
            "source_id": f"src_{sequence}",
            "provider": "provider",
            "subject": "subject",
            "course_year": "2026",
            "sequence": sequence,
            "telegram_message_id": sequence,
            "title": f"Lesson {sequence}",
            "duration_seconds": 2.0,
        },
        "asr": {"engine": "test", "model": "test", "language": "ru"},
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "один"},
            {"id": 1, "start": 1.0, "end": 2.0, "text": "два"},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class RetrievalContractTests(unittest.TestCase):
    def test_default_search_threshold_matches_frozen_v031(self):
        from retrieval.config import SEMANTIC_MIN_SIMILARITY
        from retrieval.engine import SearchEngine
        self.assertEqual(SEMANTIC_MIN_SIMILARITY, 0.458)
        self.assertEqual(SearchEngine.__init__.__defaults__[0], 0.458)


class CourseReleaseTests(unittest.TestCase):
    def test_manifest_reports_explicit_missing_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            canonical(transcripts / "001.json", 1)
            canonical(transcripts / "003.json", 3)
            manifest_path = root / "course.json"
            manifest_path.write_text(json.dumps({
                "schema_version": 1,
                "course_id": "course-test",
                "title": "Course Test",
                "provider": "provider",
                "subject": "subject",
                "course_year": "2026",
                "content_kind": "canonical_asr_v2",
                "source_root": "transcripts",
                "expected_documents": 3,
                "missing_sequences": [2],
            }), encoding="utf-8")
            report = CourseManifest.load(manifest_path).inspect(strict=True)
            self.assertFalse(report["complete"])
            self.assertEqual(report["available_documents"], 2)
            self.assertEqual(report["observed_missing_sequences"], [2])
            self.assertEqual(report["explicit_missing_sequences"], [2])

    def test_manifest_rejects_stale_declared_missing_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            for sequence in (1, 2, 3):
                canonical(transcripts / f"{sequence:03d}.json", sequence)
            manifest_path = root / "course.json"
            manifest_path.write_text(json.dumps({
                "schema_version": 1,
                "course_id": "course-test",
                "title": "Course Test",
                "provider": "provider",
                "subject": "subject",
                "course_year": "2026",
                "content_kind": "canonical_asr_v2",
                "source_root": "transcripts",
                "expected_documents": 3,
                "missing_sequences": [2],
            }), encoding="utf-8")
            manifest = CourseManifest.load(manifest_path)
            report = manifest.inspect(strict=False)
            self.assertTrue(report["observed_missing_sequences"] == [])
            self.assertFalse(report["missing_declaration_matches"])
            self.assertFalse(report["complete"])
            with self.assertRaises(ValueError):
                manifest.inspect(strict=True)

    def test_manifest_index_exclusion_must_reference_existing_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            canonical(transcripts / "001.json", 1)
            manifest_path = root / "course.json"
            manifest_path.write_text(json.dumps({
                "schema_version": 1,
                "course_id": "course-test",
                "title": "Course Test",
                "provider": "provider",
                "subject": "subject",
                "course_year": "2026",
                "content_kind": "canonical_asr_v2",
                "source_root": "transcripts",
                "expected_documents": 1,
                "missing_sequences": [],
                "index_exclude_sequences": [2],
            }), encoding="utf-8")
            manifest = CourseManifest.load(manifest_path)
            report = manifest.inspect(strict=False)
            self.assertEqual(report["invalid_index_exclude_sequences"], [2])
            with self.assertRaises(ValueError):
                manifest.inspect(strict=True)

    def test_course_catalog_and_filter_alias(self):
        with tempfile.TemporaryDirectory() as td:
            db = RetrievalDB(Path(td) / "test.db")
            try:
                db.init_schema()
                db.register_course("course-test", "canonical_asr_v2", "/tmp/course", {"title": "Course Test", "complete": False})
                rows = db.list_courses()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["course_id"], "course-test")
                self.assertEqual(rows[0]["documents"], 0)
                sql, params = db._filter_sql({"course_id": "course-test"}, "d")
                self.assertIn("d.source_id", sql)
                self.assertEqual(params, ["course-test"])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
