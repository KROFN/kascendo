from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import STOPWORDS, TOKEN_RE


@dataclass(frozen=True)
class LearningUnitCourse:
    schema_version: int
    course_id: str
    title: str
    provider: str
    subject: str
    course_year: str
    content_kind: str
    source_file: str
    source_uri: str | None
    provider_course_id: int
    expected_units: int | None
    metadata: dict[str, Any]
    manifest_path: str

    @classmethod
    def load(cls, path: str | Path) -> "LearningUnitCourse":
        p = Path(path).resolve()
        raw = json.loads(p.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("expected learning-unit Course manifest schema_version=1")
        required = (
            "course_id", "title", "provider", "subject", "course_year",
            "content_kind", "source_file", "provider_course_id",
        )
        missing = [key for key in required if not str(raw.get(key) if raw.get(key) is not None else "").strip()]
        if missing:
            raise ValueError(f"Learning Unit course manifest missing required fields: {', '.join(missing)}")
        if raw["content_kind"] != "learning_units_v1":
            raise ValueError(f"unsupported Learning Unit content_kind: {raw['content_kind']}")
        source = Path(str(raw["source_file"]))
        if not source.is_absolute():
            source = (p.parent / source).resolve()
        expected = raw.get("expected_units")
        return cls(
            schema_version=1,
            course_id=str(raw["course_id"]),
            title=str(raw["title"]),
            provider=str(raw["provider"]),
            subject=str(raw["subject"]),
            course_year=str(raw["course_year"]),
            content_kind="learning_units_v1",
            source_file=str(source),
            source_uri=str(raw["source_uri"]) if raw.get("source_uri") else None,
            provider_course_id=int(raw["provider_course_id"]),
            expected_units=int(expected) if expected is not None else None,
            metadata=dict(raw.get("metadata") or {}),
            manifest_path=str(p),
        )

    def _bundle(self) -> dict[str, Any]:
        source = Path(self.source_file)
        if not source.is_file():
            raise FileNotFoundError(f"Learning Unit source_file not found: {source}")
        raw = json.loads(source.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("expected Learning Unit bundle schema_version=1")
        if int(raw.get("course_id")) != self.provider_course_id:
            raise ValueError(
                f"provider course mismatch: manifest={self.provider_course_id} bundle={raw.get('course_id')}"
            )
        if not isinstance(raw.get("units"), list):
            raise ValueError("Learning Unit bundle units must be a list")
        return raw

    def inspect(self, strict: bool = True) -> dict[str, Any]:
        raw = self._bundle()
        units = raw["units"]
        ids: list[str] = []
        orders: list[int] = []
        invalid: list[dict[str, Any]] = []
        modes: dict[str, int] = {}
        evidence_modes: dict[str, int] = {}
        for index, unit in enumerate(units, 1):
            try:
                unit_id = str(unit["id"]).strip()
                version = int(unit["version"])
                title = str(unit["title"]).strip()
                order = int(unit["order"])
                locator = dict(unit["locator"])
                if not unit_id or not title or version < 1 or order < 1:
                    raise ValueError("invalid id/title/version/order")
                if str(locator.get("provider")) != "stepik":
                    raise ValueError("locator.provider must be stepik")
                if int(locator.get("course_id")) != self.provider_course_id:
                    raise ValueError("locator.course_id does not match course")
                lesson_id = int(locator.get("lesson_id"))
                step_start = int(locator.get("step_start"))
                step_end = int(locator.get("step_end"))
                url = str(locator.get("url") or "")
                if lesson_id < 1 or step_start < 1 or step_end < step_start:
                    raise ValueError("invalid Stepik lesson/step locator")
                if not url.startswith(f"https://stepik.org/lesson/{lesson_id}/step/"):
                    raise ValueError("locator.url does not match lesson_id")
                ids.append(unit_id)
                orders.append(order)
                mode = str(unit.get("mode") or "")
                evidence_mode = str(unit.get("evidence_mode") or "")
                modes[mode] = modes.get(mode, 0) + 1
                evidence_modes[evidence_mode] = evidence_modes.get(evidence_mode, 0) + 1
            except Exception as exc:
                invalid.append({"index": index, "id": unit.get("id"), "error": f"{type(exc).__name__}: {exc}"})
        duplicate_ids = sorted({unit_id for unit_id in ids if ids.count(unit_id) > 1})
        duplicate_orders = sorted({order for order in orders if orders.count(order) > 1})
        expected_ok = self.expected_units is None or len(units) == self.expected_units
        complete = not invalid and not duplicate_ids and not duplicate_orders and expected_ok
        report = {
            "course_id": self.course_id,
            "title": self.title,
            "content_kind": self.content_kind,
            "source_file": self.source_file,
            "manifest_path": self.manifest_path,
            "provider_course_id": self.provider_course_id,
            "expected_units": self.expected_units,
            "available_units": len(units),
            "active_units": sum(bool(unit.get("active", True)) for unit in units),
            "complete": complete,
            "duplicate_unit_ids": duplicate_ids,
            "duplicate_orders": duplicate_orders,
            "invalid_units": invalid,
            "modes": dict(sorted(modes.items())),
            "evidence_modes": dict(sorted(evidence_modes.items())),
            "first_order": min(orders) if orders else None,
            "last_order": max(orders) if orders else None,
        }
        if strict and not complete:
            raise ValueError(f"Learning Unit course validation failed: {report}")
        return report

    def units(self, active_only: bool = False) -> list[dict[str, Any]]:
        self.inspect(strict=True)
        units = list(self._bundle()["units"])
        if active_only:
            units = [unit for unit in units if bool(unit.get("active", True))]
        return sorted(units, key=lambda unit: (int(unit["order"]), str(unit["id"])))

    def unit(self, unit_id: str) -> dict[str, Any]:
        wanted = str(unit_id).strip()
        for unit in self.units(active_only=False):
            if str(unit["id"]) == wanted:
                return unit
        raise KeyError(f"Learning Unit not found: {wanted}")

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(str(text)) if len(token) >= 2 and token.lower() not in STOPWORDS]

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = int(limit)
        if limit < 1 or limit > 10:
            raise ValueError("Learning Unit search limit must be between 1 and 10")
        q_tokens = set(self._tokens(query))
        if not q_tokens:
            return []
        ranked: list[tuple[float, int, str, dict[str, Any]]] = []
        for unit in self.units(active_only=True):
            title = str(unit.get("title") or "")
            fallback = " ".join(str(x) for x in unit.get("locator", {}).get("fallback_path", []))
            instructions = " ".join(str(x) for x in unit.get("instruction", []))
            fields = {
                "title": set(self._tokens(title)),
                "fallback": set(self._tokens(fallback)),
                "instructions": set(self._tokens(instructions)),
                "id": set(self._tokens(str(unit.get("id") or ""))),
            }
            title_hits = len(q_tokens & fields["title"])
            fallback_hits = len(q_tokens & fields["fallback"])
            instruction_hits = len(q_tokens & fields["instructions"])
            id_hits = len(q_tokens & fields["id"])
            score = 4.0 * title_hits + 2.0 * fallback_hits + instruction_hits + 5.0 * id_hits
            if q_tokens <= (fields["title"] | fields["fallback"]):
                score += 2.0
            if query.casefold() in title.casefold():
                score += 4.0
            if score <= 0:
                continue
            locator = dict(unit["locator"])
            result = {
                "material_kind": "learning_unit",
                "unit_id": str(unit["id"]),
                "title": title,
                "mode": unit.get("mode"),
                "order": int(unit["order"]),
                "score": round(score, 6),
                "reason": "learning-unit lexical match",
                "source_url": locator.get("url"),
                "locator": locator,
                "instruction": list(unit.get("instruction") or []),
                "completion_criterion": dict(unit.get("completion_criterion") or {}),
                "evidence_mode": unit.get("evidence_mode"),
                "prerequisite_unit_ids": list(unit.get("prerequisite_unit_ids") or []),
            }
            ranked.append((score, int(unit["order"]), str(unit["id"]), result))
        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [row[3] for row in ranked[:limit]]

    def public_course(self) -> dict[str, Any]:
        inspection = self.inspect(strict=True)
        return {
            "course_id": self.course_id,
            "kind": self.content_kind,
            "root": self.source_file,
            "config": {
                "schema_version": self.schema_version,
                "title": self.title,
                "provider": self.provider,
                "subject": self.subject,
                "course_year": self.course_year,
                "content_kind": self.content_kind,
                "source_uri": self.source_uri,
                "provider_course_id": self.provider_course_id,
                "expected_units": self.expected_units,
                "available_units": inspection["available_units"],
                "active_units": inspection["active_units"],
                "complete": inspection["complete"],
                "metadata": self.metadata,
            },
            "documents": 0,
            "chunks": 0,
            "min_sequence": None,
            "max_sequence": None,
            "learning_units": inspection["available_units"],
            "searchable": False,
        }


def discover_learning_unit_courses(course_dir: str | Path) -> list[LearningUnitCourse]:
    root = Path(course_dir)
    if not root.is_dir():
        return []
    return [LearningUnitCourse.load(path) for path in sorted(root.glob("*.learning-units.json"))]
