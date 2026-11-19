from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BASE_URL = "https://stepik.org/api"
USER_AGENT = "K.ascendo/0.4 stepik-acquisition"
MAX_SECTIONS = 500
MAX_UNITS = 5000
MAX_LESSONS = 5000
MAX_STEPS = 50000
BATCH_SIZE = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_request(path: str, params: list[tuple[str, Any]] | None = None) -> dict[str, Any]:
    if not path.startswith("/") or "://" in path:
        raise ValueError("Stepik API path must be relative to the fixed official API base")
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read(20_000_000)
                if response.read(1):
                    raise RuntimeError("Stepik API response exceeds 20 MB safety limit")
                result = json.loads(body.decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("Stepik API response is not an object")
                return result
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Stepik request failed: {last}")


def _unique(values: list[int]) -> list[int]:
    return list(dict.fromkeys(int(value) for value in values))


def _fetch_ids(endpoint: str, ids: list[int], *, fetch: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    ids = _unique(ids)
    out: list[dict[str, Any]] = []
    for index in range(0, len(ids), BATCH_SIZE):
        chunk = ids[index:index + BATCH_SIZE]
        payload = fetch(f"/{endpoint}", [("ids[]", value) for value in chunk])
        values = payload.get(endpoint, [])
        if not isinstance(values, list):
            raise RuntimeError(f"Stepik {endpoint} response is not a list")
        out.extend(value for value in values if isinstance(value, dict))
        time.sleep(0.05)
    return out


def _require_count(name: str, values: list[Any], maximum: int) -> None:
    if len(values) > maximum:
        raise ValueError(f"Stepik course exceeds {name} safety limit: {len(values)} > {maximum}")


def acquire_structure(course_id: int, *, fetch: Callable[..., dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    course_id = int(course_id)
    fetch = fetch or _json_request
    if course_id < 1:
        raise ValueError("course_id must be positive")
    payload = fetch(f"/courses/{course_id}")
    courses = payload.get("courses", [])
    if not isinstance(courses, list) or len(courses) != 1 or int(courses[0].get("id", 0)) != course_id:
        raise RuntimeError(f"Stepik course {course_id} was not returned exactly once")
    course = courses[0]
    section_ids = _unique([int(value) for value in course.get("sections", [])])
    _require_count("sections", section_ids, MAX_SECTIONS)
    sections = _fetch_ids("sections", section_ids, fetch=fetch)
    sections.sort(key=lambda row: (int(row.get("position") or 0), int(row.get("id") or 0)))
    section_by_id = {int(row["id"]): row for row in sections}
    missing_sections = sorted(set(section_ids) - set(section_by_id))
    if missing_sections:
        raise RuntimeError(f"Stepik sections missing from API response: {missing_sections[:20]}")

    unit_ids = _unique([int(uid) for sid in section_ids for uid in section_by_id[sid].get("units", [])])
    _require_count("units", unit_ids, MAX_UNITS)
    units = _fetch_ids("units", unit_ids, fetch=fetch)
    unit_by_id = {int(row["id"]): row for row in units}
    missing_units = sorted(set(unit_ids) - set(unit_by_id))
    if missing_units:
        raise RuntimeError(f"Stepik units missing from API response: {missing_units[:20]}")

    lesson_ids = _unique([int(unit_by_id[uid]["lesson"]) for uid in unit_ids])
    _require_count("lessons", lesson_ids, MAX_LESSONS)
    lessons = _fetch_ids("lessons", lesson_ids, fetch=fetch)
    lesson_by_id = {int(row["id"]): row for row in lessons}
    missing_lessons = sorted(set(lesson_ids) - set(lesson_by_id))
    if missing_lessons:
        raise RuntimeError(f"Stepik lessons missing from API response: {missing_lessons[:20]}")

    step_ids = _unique([int(step_id) for lesson_id in lesson_ids for step_id in lesson_by_id[lesson_id].get("steps", [])])
    _require_count("steps", step_ids, MAX_STEPS)
    steps = _fetch_ids("steps", step_ids, fetch=fetch)
    step_by_id = {int(row["id"]): row for row in steps}
    missing_steps = sorted(set(step_ids) - set(step_by_id))
    if missing_steps:
        raise RuntimeError(f"Stepik steps missing from API response: {missing_steps[:20]}")

    raw = {"course": course, "sections": sections, "units": units, "lessons": lessons, "steps": steps}
    modules: list[dict[str, Any]] = []
    for section in sections:
        module = {
            "id": int(section["id"]),
            "position": int(section.get("position") or 0),
            "title": str(section.get("title") or ""),
            "description": section.get("description"),
            "lessons": [],
        }
        for unit_id in section.get("units", []):
            unit = unit_by_id[int(unit_id)]
            lesson = lesson_by_id[int(unit["lesson"])]
            normalized_steps: list[dict[str, Any]] = []
            for fallback_position, step_id in enumerate(lesson.get("steps", []), 1):
                step = step_by_id[int(step_id)]
                block = step.get("block") or {}
                position = int(step.get("position") or fallback_position)
                normalized_steps.append({
                    "step_id": int(step_id),
                    "position": position,
                    "type": block.get("name"),
                    "worth": step.get("worth"),
                    "correct_ratio": step.get("correct_ratio"),
                    "url": f"https://stepik.org/lesson/{int(lesson['id'])}/step/{position}",
                    "text": block.get("text") if block.get("name") == "text" else None,
                })
            module["lessons"].append({
                "unit_id": int(unit_id),
                "lesson_id": int(lesson["id"]),
                "position": int(unit.get("position") or 0),
                "title": str(lesson.get("title") or ""),
                "url": f"https://stepik.org/lesson/{int(lesson['id'])}/step/1",
                "canonical_url": lesson.get("canonical_url"),
                "time_to_complete_seconds": lesson.get("time_to_complete"),
                "steps": normalized_steps,
            })
        module["lessons"].sort(key=lambda row: (row["position"], row["lesson_id"]))
        modules.append(module)
    normalized = {
        "schema_version": 1,
        "course": {
            "id": course_id,
            "title": str(course.get("title") or ""),
            "summary": course.get("summary"),
            "url": f"https://stepik.org/course/{course_id}",
            "updated_at": course.get("update_date"),
            "total_units": course.get("total_units"),
        },
        "modules": modules,
    }
    return raw, normalized


def build_draft_units(normalized: dict[str, Any]) -> dict[str, Any]:
    course = dict(normalized["course"])
    course_id = int(course["id"])
    flattened = [lesson for module in normalized.get("modules", []) for lesson in module.get("lessons", [])]
    units: list[dict[str, Any]] = []
    previous: str | None = None
    for lesson in flattened:
        lesson_id = int(lesson["lesson_id"])
        stepik_unit_id = int(lesson["unit_id"])
        step_count = len(lesson.get("steps", []))
        if step_count < 1:
            continue
        order = len(units) + 1
        unit_id = f"STEP-{course_id}-U{stepik_unit_id}"
        types = Counter(str(step.get("type") or "unknown") for step in lesson.get("steps", []))
        only_text = set(types) <= {"text"}
        estimate_seconds = lesson.get("time_to_complete_seconds")
        if isinstance(estimate_seconds, (int, float)) and estimate_seconds > 0:
            estimated_minutes = max(5, min(180, round(float(estimate_seconds) / 300) * 5))
        else:
            estimated_minutes = max(10, min(120, round(step_count * 2 / 5) * 5))
        units.append({
            "id": unit_id,
            "version": 1,
            "parent_skill_id": f"stepik_course_{course_id}",
            "title": str(lesson.get("title") or f"Lesson {lesson_id}"),
            "purpose": "Draft Learning Unit generated from one Stepik lesson; requires course-level curation before activation.",
            "mode": "theory" if only_text else "mixed",
            "resource_id": f"stepik_course_{course_id}",
            "locator": {
                "provider": "stepik",
                "course_id": course_id,
                "lesson_id": lesson_id,
                "step_start": 1,
                "step_end": step_count,
                "url": str(lesson["url"]),
                "fallback_path": [str(course.get("title") or course_id), str(lesson.get("title") or lesson_id)],
            },
            "instruction": [
                f"Открой урок «{str(lesson.get('title') or lesson_id)}» по прямой ссылке.",
                "Пройди указанный урок Stepik по шагам по порядку.",
            ],
            "completion_criterion": {
                "kind": "lesson_complete",
                "criteria": ["пройден весь указанный урок Stepik"],
            },
            "evidence_mode": "content_progress_only",
            "prerequisite_unit_ids": [previous] if previous else [],
            "estimated_minutes": estimated_minutes,
            "active": False,
            "order": order,
            "source_facts": {
                "stepik_unit_id": stepik_unit_id,
                "lesson_step_count": step_count,
                "step_types": dict(sorted(types.items())),
                "draft_not_curated": True,
            },
        })
        previous = unit_id
    return {
        "schema_version": 1,
        "course_id": course_id,
        "skill_id": f"stepik_course_{course_id}",
        "draft": True,
        "units": units,
    }


def acquire_to_directory(
    course_id: int,
    out_dir: str | Path,
    *,
    fetch: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    raw, normalized = acquire_structure(course_id, fetch=fetch)
    draft = build_draft_units(normalized)
    prefix = f"stepik-course-{int(course_id)}"
    raw_path = out / f"{prefix}.raw.json"
    map_path = out / f"{prefix}.map.json"
    draft_path = out / f"{prefix}.draft-units.json"
    index_path = out / f"{prefix}.index.md"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    map_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {normalized['course']['title']}", ""]
    for module in normalized["modules"]:
        lines.append(f"## {module['position']}. {module['title']}")
        for lesson in module["lessons"]:
            types = Counter(str(step.get("type") or "unknown") for step in lesson["steps"])
            lines.append(
                f"- {lesson['position']}. {lesson['title']} | lesson={lesson['lesson_id']} | "
                f"steps={len(lesson['steps'])} | types={dict(sorted(types.items()))} | {lesson['url']}"
            )
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = {
        "sections": len(raw["sections"]),
        "units": len(raw["units"]),
        "lessons": len(raw["lessons"]),
        "steps": len(raw["steps"]),
        "draft_units": len(draft["units"]),
    }
    manifest = {
        "schema_version": 1,
        "provider": "stepik",
        "provider_course_id": int(course_id),
        "source_uri": f"https://stepik.org/course/{int(course_id)}",
        "provider_updated_at": normalized["course"].get("updated_at"),
        "acquired_at": now_iso(),
        "counts": counts,
        "activation_status": "staged_not_active",
        "network_contract": "fixed https://stepik.org/api read-only; no credentials",
        "files": {},
    }
    for path in (raw_path, map_path, draft_path, index_path):
        manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    manifest_path = out / f"{prefix}.acquisition.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"manifest": str(manifest_path), **manifest}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Acquire one public Stepik course into a non-active staging directory.")
    parser.add_argument("course_id", type=int)
    parser.add_argument("out_dir")
    args = parser.parse_args()
    print(json.dumps(acquire_to_directory(args.course_id, args.out_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
