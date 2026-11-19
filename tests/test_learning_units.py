from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from retrieval.learning_units import LearningUnitCourse


class LearningUnitCourseTests(unittest.TestCase):
    def _write(self, root: Path, *, bundle_course_id: int = 58852, duplicate: bool = False) -> Path:
        units = [
            {
                "id": "PY-B01",
                "version": 1,
                "parent_skill_id": "career_python_basics",
                "title": "Введение",
                "mode": "theory",
                "locator": {
                    "provider": "stepik",
                    "course_id": bundle_course_id,
                    "lesson_id": 284315,
                    "step_start": 1,
                    "step_end": 10,
                    "url": "https://stepik.org/lesson/284315/step/1",
                },
                "instruction": ["Открой урок"],
                "completion_criterion": {"kind": "lesson_complete", "criteria": ["пройден урок"]},
                "evidence_mode": "content_progress_only",
                "prerequisite_unit_ids": [],
                "active": True,
                "order": 1,
            }
        ]
        if duplicate:
            units.append({**units[0]})
        source = root / "units.json"
        source.write_text(json.dumps({"schema_version": 1, "course_id": bundle_course_id, "skill_id": "career_python_basics", "units": units}, ensure_ascii=False), encoding="utf-8")
        manifest = root / "course.learning-units.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "course_id": "stepik-python-beginner-58852",
            "title": "Поколение Python",
            "provider": "Stepik / BEEGEEK",
            "subject": "python",
            "course_year": "self-paced",
            "content_kind": "learning_units_v1",
            "source_file": "units.json",
            "source_uri": "https://stepik.org/course/58852",
            "provider_course_id": 58852,
            "expected_units": len(units),
        }, ensure_ascii=False), encoding="utf-8")
        return manifest

    def test_valid_bundle_inspect_and_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            course = LearningUnitCourse.load(self._write(Path(td)))
            report = course.inspect(strict=True)
            self.assertTrue(report["complete"])
            self.assertEqual(report["available_units"], 1)
            self.assertEqual(report["active_units"], 1)
            self.assertEqual(course.unit("PY-B01")["locator"]["lesson_id"], 284315)
            public = course.public_course()
            self.assertFalse(public["searchable"])
            self.assertEqual(public["learning_units"], 1)

    def test_title_focused_search_returns_exact_locator(self):
        with tempfile.TemporaryDirectory() as td:
            course = LearningUnitCourse.load(self._write(Path(td)))
            hits = course.search("введение python", limit=3)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["unit_id"], "PY-B01")
            self.assertEqual(hits[0]["material_kind"], "learning_unit")
            self.assertEqual(hits[0]["locator"]["lesson_id"], 284315)
            self.assertEqual(hits[0]["source_url"], "https://stepik.org/lesson/284315/step/1")

    def test_provider_course_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            course = LearningUnitCourse.load(self._write(Path(td), bundle_course_id=999))
            with self.assertRaises(ValueError):
                course.inspect(strict=True)

    def test_duplicate_unit_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            course = LearningUnitCourse.load(self._write(Path(td), duplicate=True))
            report = course.inspect(strict=False)
            self.assertEqual(report["duplicate_unit_ids"], ["PY-B01"])
            self.assertEqual(report["duplicate_orders"], [1])
            self.assertFalse(report["complete"])
            with self.assertRaises(ValueError):
                course.inspect(strict=True)


class TrackedStepikCourseTests(unittest.TestCase):
    def test_tracked_stepik_course_is_complete_and_addressable(self):
        root = Path(__file__).resolve().parents[1]
        course = LearningUnitCourse.load(root / "courses" / "stepik-python-beginner-58852.learning-units.json")
        report = course.inspect(strict=True)
        self.assertEqual(report["available_units"], 2)
        self.assertEqual(report["active_units"], 2)
        first = course.unit("PY-B01")
        self.assertEqual(first["locator"]["course_id"], 58852)
        self.assertEqual(first["locator"]["lesson_id"], 284315)
        self.assertEqual(first["locator"]["url"], "https://stepik.org/lesson/284315/step/1")
        hits = course.search("цикл for", limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["unit_id"], "PY-B15")
        self.assertEqual(hits[0]["source_url"], "https://stepik.org/lesson/265118/step/1")


if __name__ == "__main__":
    unittest.main()
