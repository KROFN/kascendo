from __future__ import annotations

import fcntl
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .inbox import CourseInboxStore
from .learning_units import discover_learning_unit_courses
from .stepik_acquire import acquire_to_directory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INBOX_DB = Path(os.environ.get("KASCENDO_INBOX_DB", str(ROOT / "data/state/kascendo_inbox.db"))).resolve()
DEFAULT_COURSE_DIR = Path(os.environ.get("KASCENDO_COURSE_DIR", str(ROOT / "courses"))).resolve()
DEFAULT_STAGING_ROOT = Path(os.environ.get("KASCENDO_INBOX_ACQUIRED_ROOT", str(ROOT / "data/inbox/acquired"))).resolve()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def acquire_inbox_item(
    item_id: int,
    *,
    inbox_db: str | Path = DEFAULT_INBOX_DB,
    course_dir: str | Path = DEFAULT_COURSE_DIR,
    staging_root: str | Path = DEFAULT_STAGING_ROOT,
    acquire_fn: Callable[[int, str | Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item_id = int(item_id)
    if item_id < 1:
        raise ValueError("item_id must be positive")
    acquire_fn = acquire_fn or acquire_to_directory
    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    courses = discover_learning_unit_courses(Path(course_dir).resolve())
    store = CourseInboxStore(Path(inbox_db).resolve())
    try:
        item = store.get(item_id, courses=courses)
        if item["source_kind"] != "stepik_course":
            raise ValueError("managed acquisition currently supports only Stepik course Inbox items")
        course_id = int(item.get("provider_course_id") or 0)
        if course_id < 1:
            raise ValueError("Inbox Stepik item has invalid provider_course_id")
        lock_dir = root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / f"stepik-{course_id}.lock"
        with lock_path.open("a+b") as lock_file:
            os.chmod(lock_path, 0o600)
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Stepik acquisition already running for course {course_id}") from exc
            out_dir = root / f"stepik-{course_id}-{_stamp()}-{secrets.token_hex(4)}"
            acquisition = acquire_fn(course_id, out_dir)
            manifest_path = Path(str(acquisition.get("manifest") or "")).resolve()
            if not manifest_path.is_file():
                raise RuntimeError("Stepik acquisition did not produce a manifest")
            attached = store.attach_acquisition(item_id, manifest_path, courses, root)
            return {
                "item": attached,
                "acquisition": acquisition,
                "staging_dir": str(out_dir),
                "auto_activation": False,
            }
    finally:
        store.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Acquire and attach one Stepik Course Inbox item without activating it.")
    parser.add_argument("item_id", type=int)
    args = parser.parse_args()
    print(json.dumps(acquire_inbox_item(args.item_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
