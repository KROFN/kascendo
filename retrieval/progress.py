from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .learning_units import LearningUnitCourse


EVIDENCE_KIND_BY_MODE = {
    "practice_evidence": "practice_assessment",
    "build_evidence": "build",
    "mock_evidence": "mock",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressStore:
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
            CREATE TABLE IF NOT EXISTS unit_progress(
                course_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_version INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('not_started','in_progress','completed','skipped')),
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                locator_snapshot_json TEXT NOT NULL,
                PRIMARY KEY(course_id, unit_id)
            );
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_version INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','completed','cancelled')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                locator_snapshot_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_course_status ON sessions(course_id,status,unit_id);
            CREATE TABLE IF NOT EXISTS evidence(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_version INTEGER NOT NULL,
                skill_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                result_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(course_id, unit_id, unit_version)
            );
            """
        )
        self.conn.commit()

    def _row(self, course_id: str, unit_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM unit_progress WHERE course_id=? AND unit_id=?",
            (course_id, unit_id),
        ).fetchone()

    @staticmethod
    def _row_public(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _completed_ids(self, course_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT unit_id FROM unit_progress WHERE course_id=? AND status='completed'",
            (course_id,),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def next_unit(self, course: LearningUnitCourse) -> dict[str, Any] | None:
        units = course.units(active_only=True)
        by_id = {str(unit["id"]): unit for unit in units}
        in_progress = self.conn.execute(
            "SELECT * FROM unit_progress WHERE course_id=? AND status='in_progress' ORDER BY started_at,unit_id",
            (course.course_id,),
        ).fetchall()
        for row in in_progress:
            unit = by_id.get(str(row["unit_id"]))
            if unit is None:
                continue
            if int(row["unit_version"]) != int(unit["version"]):
                raise RuntimeError(
                    f"Learning Unit version drift for {unit['id']}: progress={row['unit_version']} current={unit['version']}"
                )
            return {"unit": unit, "progress": dict(row), "selection": "in_progress"}
        completed = self._completed_ids(course.course_id)
        for unit in units:
            unit_id = str(unit["id"])
            row = self._row(course.course_id, unit_id)
            if row is not None and str(row["status"]) in {"completed", "skipped"}:
                continue
            prerequisites = [str(x) for x in unit.get("prerequisite_unit_ids") or []]
            if all(prerequisite in completed for prerequisite in prerequisites):
                return {"unit": unit, "progress": self._row_public(row), "selection": "next_eligible"}
        return None

    def start(self, course: LearningUnitCourse, unit_id: str = "") -> dict[str, Any]:
        selected = course.unit(unit_id) if str(unit_id).strip() else None
        if selected is None:
            next_item = self.next_unit(course)
            if next_item is None:
                raise ValueError("no eligible Learning Unit")
            selected = next_item["unit"]
        if not bool(selected.get("active", True)):
            raise ValueError(f"Learning Unit is inactive: {selected['id']}")
        wanted = str(selected["id"])
        existing = self._row(course.course_id, wanted)
        if existing is not None:
            if int(existing["unit_version"]) != int(selected["version"]):
                raise RuntimeError(
                    f"Learning Unit version drift for {wanted}: progress={existing['unit_version']} current={selected['version']}"
                )
            if str(existing["status"]) == "completed":
                raise ValueError(f"Learning Unit already completed: {wanted}")
            if str(existing["status"]) == "in_progress":
                return {"unit": selected, "progress": dict(existing), "already_in_progress": True}
        completed = self._completed_ids(course.course_id)
        prerequisites = [str(x) for x in selected.get("prerequisite_unit_ids") or []]
        missing = [prerequisite for prerequisite in prerequisites if prerequisite not in completed]
        if missing:
            raise ValueError(f"Learning Unit prerequisites not completed: {', '.join(missing)}")
        now = now_iso()
        locator_json = json.dumps(selected["locator"], ensure_ascii=False, sort_keys=True)
        with self.conn:
            self.conn.execute(
                """INSERT INTO unit_progress(course_id,unit_id,unit_version,status,started_at,completed_at,updated_at,locator_snapshot_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(course_id,unit_id) DO UPDATE SET
                     unit_version=excluded.unit_version,status='in_progress',started_at=excluded.started_at,
                     completed_at=NULL,updated_at=excluded.updated_at,locator_snapshot_json=excluded.locator_snapshot_json""",
                (course.course_id, wanted, int(selected["version"]), "in_progress", now, None, now, locator_json),
            )
            cursor = self.conn.execute(
                "INSERT INTO sessions(course_id,unit_id,unit_version,status,started_at,locator_snapshot_json) VALUES(?,?,?,?,?,?)",
                (course.course_id, wanted, int(selected["version"]), "active", now, locator_json),
            )
        return {
            "unit": selected,
            "progress": self._row_public(self._row(course.course_id, wanted)),
            "session_id": int(cursor.lastrowid),
            "already_in_progress": False,
        }

    def finish(self, course: LearningUnitCourse, unit_id: str, evidence_result: Any = None) -> dict[str, Any]:
        unit = course.unit(unit_id)
        wanted = str(unit["id"])
        row = self._row(course.course_id, wanted)
        if row is None or str(row["status"]) == "not_started":
            raise ValueError(f"Learning Unit is not in progress: {wanted}")
        if int(row["unit_version"]) != int(unit["version"]):
            raise RuntimeError(
                f"Learning Unit version drift for {wanted}: progress={row['unit_version']} current={unit['version']}"
            )
        evidence_mode = str(unit.get("evidence_mode") or "content_progress_only")
        if evidence_result is not None and evidence_mode == "content_progress_only":
            raise ValueError(f"Learning Unit does not accept Evidence: {wanted}")
        now = now_iso()
        already_completed = str(row["status"]) == "completed"
        evidence_id: int | None = None
        with self.conn:
            if not already_completed:
                if str(row["status"]) != "in_progress":
                    raise ValueError(f"Learning Unit cannot be finished from status {row['status']}: {wanted}")
                self.conn.execute(
                    "UPDATE unit_progress SET status='completed',completed_at=?,updated_at=? WHERE course_id=? AND unit_id=?",
                    (now, now, course.course_id, wanted),
                )
                active = self.conn.execute(
                    "SELECT id FROM sessions WHERE course_id=? AND unit_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (course.course_id, wanted),
                ).fetchone()
                if active is not None:
                    self.conn.execute(
                        "UPDATE sessions SET status='completed',finished_at=? WHERE id=?",
                        (now, int(active["id"])),
                    )
            if evidence_result is not None:
                kind = EVIDENCE_KIND_BY_MODE.get(evidence_mode)
                if kind is None:
                    raise ValueError(f"unsupported evidence_mode: {evidence_mode}")
                self.conn.execute(
                    """INSERT INTO evidence(course_id,unit_id,unit_version,skill_id,kind,result_json,recorded_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(course_id,unit_id,unit_version) DO NOTHING""",
                    (
                        course.course_id,
                        wanted,
                        int(unit["version"]),
                        str(unit["parent_skill_id"]),
                        kind,
                        json.dumps(evidence_result, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                existing_evidence = self.conn.execute(
                    "SELECT id FROM evidence WHERE course_id=? AND unit_id=? AND unit_version=?",
                    (course.course_id, wanted, int(unit["version"])),
                ).fetchone()
                evidence_id = int(existing_evidence["id"]) if existing_evidence is not None else None
        return {
            "unit": unit,
            "progress": self._row_public(self._row(course.course_id, wanted)),
            "already_completed": already_completed,
            "evidence_id": evidence_id,
            "evidence_pending": evidence_mode in EVIDENCE_KIND_BY_MODE and evidence_id is None,
            "competence_mutated": False,
        }

    def progress(self, course: LearningUnitCourse) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT * FROM unit_progress WHERE course_id=? ORDER BY unit_id",
            (course.course_id,),
        ).fetchall()
        evidence = self.conn.execute(
            "SELECT id,course_id,unit_id,unit_version,skill_id,kind,result_json,recorded_at FROM evidence WHERE course_id=? ORDER BY id",
            (course.course_id,),
        ).fetchall()
        counts = {"not_started": 0, "in_progress": 0, "completed": 0, "skipped": 0}
        by_unit = {str(row["unit_id"]): dict(row) for row in rows}
        for unit in course.units(active_only=True):
            status = str(by_unit.get(str(unit["id"]), {}).get("status", "not_started"))
            counts[status] = counts.get(status, 0) + 1
        evidence_public = []
        for row in evidence:
            item = dict(row)
            item["result"] = json.loads(str(item.pop("result_json")))
            evidence_public.append(item)
        next_item = self.next_unit(course)
        return {
            "course_id": course.course_id,
            "counts": counts,
            "unit_progress": list(by_unit.values()),
            "evidence": evidence_public,
            "next_unit": next_item,
            "competence_state": "not_managed_by_rc2a",
        }

    def status(self) -> dict[str, Any]:
        counts = {}
        for table in ("unit_progress", "sessions", "evidence"):
            counts[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {"path": str(self.path), **counts}
