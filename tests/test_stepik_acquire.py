from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from retrieval.stepik_acquire import acquire_structure, acquire_to_directory, build_draft_units


class FakeStepik:
    def __call__(self, path: str, params=None):
        if path == "/courses/123":
            return {"courses": [{
                "id": 123,
                "title": "Demo",
                "summary": "Summary",
                "sections": [10],
                "total_units": 2,
                "update_date": "2026-09-10T00:00:00Z",
            }]}
        ids = [int(value) for key, value in (params or []) if key == "ids[]"]
        if path == "/sections":
            return {"sections": [{"id": 10, "position": 1, "title": "S1", "description": "", "units": [20, 21]}]}
        if path == "/units":
            rows = {
                20: {"id": 20, "position": 1, "lesson": 30},
                21: {"id": 21, "position": 2, "lesson": 31},
            }
            return {"units": [rows[value] for value in ids]}
        if path == "/lessons":
            rows = {
                30: {"id": 30, "title": "Intro", "steps": [40], "time_to_complete": 600, "canonical_url": None},
                31: {"id": 31, "title": "Practice", "steps": [41, 42], "time_to_complete": 900, "canonical_url": None},
            }
            return {"lessons": [rows[value] for value in ids]}
        if path == "/steps":
            rows = {
                40: {"id": 40, "position": 1, "worth": 0, "correct_ratio": None, "block": {"name": "text", "text": "hello"}},
                41: {"id": 41, "position": 1, "worth": 1, "correct_ratio": 0.5, "block": {"name": "code", "text": None}},
                42: {"id": 42, "position": 2, "worth": 1, "correct_ratio": 0.4, "block": {"name": "choice", "text": None}},
            }
            return {"steps": [rows[value] for value in ids]}
        raise AssertionError((path, params))


class StepikAcquisitionTests(unittest.TestCase):
    def test_structure_and_draft_units_are_bounded_and_non_active(self):
        with mock.patch("retrieval.stepik_acquire.time.sleep", return_value=None):
            raw, normalized = acquire_structure(123, fetch=FakeStepik())
        self.assertEqual(len(raw["sections"]), 1)
        self.assertEqual(len(raw["units"]), 2)
        self.assertEqual(len(raw["lessons"]), 2)
        self.assertEqual(len(raw["steps"]), 3)
        self.assertEqual(normalized["course"]["updated_at"], "2026-09-10T00:00:00Z")
        draft = build_draft_units(normalized)
        self.assertTrue(draft["draft"])
        self.assertEqual([unit["id"] for unit in draft["units"]], ["STEP-123-U20", "STEP-123-U21"])
        self.assertEqual([unit["order"] for unit in draft["units"]], [1, 2])
        self.assertEqual(draft["units"][0]["mode"], "theory")
        self.assertEqual(draft["units"][1]["mode"], "mixed")
        self.assertTrue(all(unit["active"] is False for unit in draft["units"]))
        self.assertTrue(all(unit["evidence_mode"] == "content_progress_only" for unit in draft["units"]))
        self.assertEqual(draft["units"][1]["prerequisite_unit_ids"], ["STEP-123-U20"])
        self.assertEqual(draft["units"][1]["locator"]["url"], "https://stepik.org/lesson/31/step/1")

    def test_zero_step_service_lesson_is_skipped_without_order_gap(self):
        normalized = {
            "course": {"id": 123, "title": "Demo"},
            "modules": [{
                "lessons": [
                    {"unit_id": 20, "lesson_id": 30, "title": "First", "url": "https://stepik.org/lesson/30/step/1", "steps": [{"type": "text"}]},
                    {"unit_id": 21, "lesson_id": 31, "title": "@!!!Service", "url": "https://stepik.org/lesson/31/step/1", "steps": []},
                    {"unit_id": 22, "lesson_id": 32, "title": "Second", "url": "https://stepik.org/lesson/32/step/1", "steps": [{"type": "code"}]},
                ]
            }],
        }
        draft = build_draft_units(normalized)
        self.assertEqual([unit["id"] for unit in draft["units"]], ["STEP-123-U20", "STEP-123-U22"])
        self.assertEqual([unit["order"] for unit in draft["units"]], [1, 2])
        self.assertEqual(draft["units"][1]["prerequisite_unit_ids"], ["STEP-123-U20"])

    def test_acquisition_writes_manifest_with_hashes_without_activation(self):
        fake = FakeStepik()
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("retrieval.stepik_acquire.time.sleep", return_value=None):
            result = acquire_to_directory(123, td, fetch=fake)
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["activation_status"], "staged_not_active")
            self.assertEqual(manifest["counts"], {"sections": 1, "units": 2, "lessons": 2, "steps": 3, "draft_units": 2})
            self.assertEqual(set(manifest["files"]), {
                "stepik-course-123.raw.json",
                "stepik-course-123.map.json",
                "stepik-course-123.draft-units.json",
                "stepik-course-123.index.md",
            })
            for info in manifest["files"].values():
                self.assertEqual(len(info["sha256"]), 64)
                self.assertGreater(info["bytes"], 0)
            draft = json.loads((Path(td) / "stepik-course-123.draft-units.json").read_text(encoding="utf-8"))
            self.assertTrue(all(unit["active"] is False for unit in draft["units"]))

    def test_missing_api_objects_fail_closed(self):
        fake = FakeStepik()
        def broken(path: str, params=None):
            result = fake(path, params)
            if path == "/lessons":
                return {"lessons": result["lessons"][:1]}
            return result
        with mock.patch("retrieval.stepik_acquire.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                acquire_structure(123, fetch=broken)


if __name__ == "__main__":
    unittest.main()
