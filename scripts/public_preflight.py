"""Prepublication allowlist and common secret scanner for this clean checkout.

This is a guardrail, not a substitute for manual review.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore", "README.md", "requirements.txt",
    "requirements-dev.txt", "pyproject.toml", "serve.py",
}
ALLOWED_DIRS = {"retrieval", "tests", "docs", "scripts", ".github", "courses", "data", "examples"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".ruff_cache", ".pytest_cache"}
DATA_ALLOWLIST = {
    "data/courses/stepik-python-beginner-58852/python-learning-units-v0.json",
}
COURSE_ALLOWLIST = {"courses/stepik-python-beginner-58852.learning-units.json"}
PATTERNS = {
    "private-key-header": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{15,}"),
    "OpenAI-like token": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{15,}"),
    "Google key": re.compile(r"AIza[A-Za-z0-9_-]{25,}"),
}


def list_files() -> list[Path]:
    # Git staged/committed files are authoritative after repository init.
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=False
    )
    if proc.returncode == 0 and proc.stdout:
        return [ROOT / entry.decode("utf-8") for entry in proc.stdout.split(b"\0") if entry]
    return [
        p for p in ROOT.rglob("*")
        if p.is_file() and not set(p.relative_to(ROOT).parts) & SKIP_DIRS
    ]


def validate() -> None:
    files = list_files()
    problems = []
    for path in files:
        name = path.relative_to(ROOT).as_posix()
        parts = path.relative_to(ROOT).parts
        if not path.exists():
            problems.append(f"missing: {name}")
            continue
        if set(parts) & SKIP_DIRS:
            problems.append(f"excluded directory: {name}")
        if len(parts) == 1 and name not in ROOT_FILES:
            problems.append(f"unapproved root file: {name}")
        if len(parts) > 1 and parts[0] not in ALLOWED_DIRS:
            problems.append(f"unapproved directory: {name}")
        if name.startswith("data/") and name not in DATA_ALLOWLIST:
            problems.append(f"private data path: {name}")
        if name.startswith("courses/") and name not in COURSE_ALLOWLIST:
            problems.append(f"private course path: {name}")
        if path.stat().st_size > 200_000:
            problems.append(f"oversized tracked file: {name}")
        if path.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".jsonl"}:
            problems.append(f"forbidden file type: {name}")
        if path.is_symlink():
            problems.append(f"symlink requires review: {name}")
            continue
        try:
            contents = path.read_text("utf-8")
        except UnicodeError:
            problems.append(f"binary or non-UTF8 file: {name}")
            continue
        for label, regex in PATTERNS.items():
            if regex.search(contents):
                problems.append(f"possible {label}: {name}")
    if problems:
        raise SystemExit("PUBLIC RELEASE BLOCKED:\n" + "\n".join(sorted(set(problems))))
    print(f"PUBLIC PREFLIGHT PASS: {len(files)} reviewed files")


if __name__ == "__main__":
    validate()
