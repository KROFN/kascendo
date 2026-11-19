from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from retrieval.learning_units import LearningUnitCourse
from retrieval.progress import ProgressStore


def write_course(root: Path) -> LearningUnitCourse:
    units = [
        {
            "id": "U1",
            "version": 1,
            "parent_skill_id": "skill-python",
            "title": "Theory",
            "mode": "theory",
            "locator": {
                "provider": "stepik",
                "course_id": 58852,
                "lesson_id": 1001,
                "step_start": 1,
                "step_end": 3,
                "url": "https://stepik.org/lesson/1001/step/1",
            },
            "instruction": ["read"],
            "completion_criterion": {"kind": "lesson_complete", "criteria": ["done"]},
            "evidence_mode": "content_progress_only",
            "prerequisite_unit_ids": [],
            "active": True,
            "order": 1,
        },
        {
            "id": "U2",
            "version": 1,
            "parent_skill_id": "skill-python",
            "title": "Assessment",
            "mode": "assessment",
            "locator": {
                "provider": "stepik",
                "course_id": 58852,
                "lesson_id": 1002,
                "step_start": 1,
                "step_end": 2,
                "url": "https://stepik.org/lesson/1002/step/1",
            },
            "instruction": ["solve"],
            "completion_criterion": {"kind": "lesson_complete", "criteria": ["done"]},
            "evidence_mode": "practice_evidence",
            "prerequisite_unit_ids": ["U1"],
            "active": True,
            "order": 2,
        },
    ]
    (root / "units.json").write_text(
        json.dumps({"schema_version": 1, "course_id": 58852, "skill_id": "skill-python", "units": units}),
        encoding="utf-8",
    )
    manifest = root / "course.learning-units.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "course_id": "course-test",
            "title": "Course",
            "provider": "Stepik",
            "subject": "python",
            "course_year": "self-paced",
            "content_kind": "learning_units_v1",
            "source_file": "units.json",
            "source_uri": "https://stepik.org/course/58852",
            "provider_course_id": 58852,
            "expected_units": 2,
        }),
        encoding="utf-8",
    )
    return LearningUnitCourse.load(manifest)


class ProgressStoreTests(unittest.TestCase):
    def test_next_start_finish_preserves_competence_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            course = write_course(root)
            store = ProgressStore(root / "state.db")
            try:
                self.assertEqual((root / "state.db").stat().st_mode & 0o777, 0o600)
                first = store.next_unit(course)
                self.assertEqual(first["unit"]["id"], "U1")
                self.assertEqual(first["selection"], "next_eligible")
                started = store.start(course)
                self.assertEqual(started["unit"]["id"], "U1")
                self.assertFalse(started["already_in_progress"])
                again = store.start(course, "U1")
                self.assertTrue(again["already_in_progress"])
                with self.assertRaises(ValueError):
                    store.start(course, "U2")
                finished = store.finish(course, "U1")
                self.assertEqual(finished["progress"]["status"], "completed")
                self.assertFalse(finished["competence_mutated"])
                self.assertIsNone(finished["evidence_id"])
                second = store.next_unit(course)
                self.assertEqual(second["unit"]["id"], "U2")
                state = store.progress(course)
                self.assertEqual(state["counts"], {"not_started": 1, "in_progress": 0, "completed": 1, "skipped": 0})
                self.assertEqual(state["competence_state"], "not_managed_by_rc2a")
            finally:
                store.close()

    def test_assessment_evidence_is_separate_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            course = write_course(root)
            store = ProgressStore(root / "state.db")
            try:
                store.start(course, "U1")
                store.finish(course, "U1")
                store.start(course, "U2")
                finished = store.finish(course, "U2")
                self.assertTrue(finished["evidence_pending"])
                self.assertFalse(finished["competence_mutated"])
                evidence = store.finish(course, "U2", {"result": "passed", "score": 8})
                self.assertFalse(evidence["evidence_pending"])
                self.assertIsInstance(evidence["evidence_id"], int)
                same = store.finish(course, "U2", {"result": "passed", "score": 8})
                self.assertEqual(same["evidence_id"], evidence["evidence_id"])
                state = store.progress(course)
                self.assertEqual(len(state["evidence"]), 1)
                self.assertEqual(state["evidence"][0]["kind"], "practice_assessment")
                self.assertEqual(state["evidence"][0]["result"]["score"], 8)
            finally:
                store.close()

    def test_theory_rejects_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            course = write_course(root)
            store = ProgressStore(root / "state.db")
            try:
                store.start(course, "U1")
                with self.assertRaises(ValueError):
                    store.finish(course, "U1", {"result": "passed"})
                self.assertEqual(store.progress(course)["counts"]["in_progress"], 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
