from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapter import ASRAdapter, CanonicalDocument


@dataclass(frozen=True)
class CourseManifest:
    schema_version: int
    course_id: str
    title: str
    provider: str
    subject: str
    course_year: str
    content_kind: str
    source_root: str
    expected_documents: int | None
    source_uri: str | None
    missing_sequences: tuple[int, ...]
    index_exclude_sequences: tuple[int, ...]
    metadata: dict[str, Any]
    manifest_path: str

    @classmethod
    def load(cls, path: str | Path) -> "CourseManifest":
        p = Path(path).resolve()
        raw = json.loads(p.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("expected Course manifest schema_version=1")
        required = ("course_id", "title", "provider", "subject", "course_year", "content_kind", "source_root")
        missing = [k for k in required if not str(raw.get(k) or "").strip()]
        if missing:
            raise ValueError(f"Course manifest missing required fields: {', '.join(missing)}")
        if raw["content_kind"] != "canonical_asr_v2":
            raise ValueError(f"unsupported content_kind: {raw['content_kind']}")
        root = Path(str(raw["source_root"]))
        if not root.is_absolute():
            root = (p.parent / root).resolve()
        expected = raw.get("expected_documents")
        return cls(
            schema_version=1,
            course_id=str(raw["course_id"]),
            title=str(raw["title"]),
            provider=str(raw["provider"]),
            subject=str(raw["subject"]),
            course_year=str(raw["course_year"]),
            content_kind=str(raw["content_kind"]),
            source_root=str(root),
            expected_documents=int(expected) if expected is not None else None,
            source_uri=str(raw["source_uri"]) if raw.get("source_uri") else None,
            missing_sequences=tuple(sorted({int(x) for x in raw.get("missing_sequences", [])})),
            index_exclude_sequences=tuple(sorted({int(x) for x in raw.get("index_exclude_sequences", [])})),
            metadata=dict(raw.get("metadata") or {}),
            manifest_path=str(p),
        )

    def discover(self) -> list[Path]:
        root = Path(self.source_root)
        if not root.is_dir():
            raise FileNotFoundError(f"course source_root not found: {root}")
        return sorted(root.glob("*.json"))

    def inspect(self, strict: bool = True) -> dict[str, Any]:
        paths = self.discover()
        documents: list[CanonicalDocument] = []
        errors: list[dict[str, str]] = []
        for p in paths:
            try:
                documents.append(ASRAdapter.load(p, corpus=self.course_id))
            except Exception as exc:
                errors.append({"path": str(p), "error": f"{type(exc).__name__}: {exc}"})
        seqs = [d.sequence for d in documents if d.sequence is not None]
        counts: dict[int, int] = {}
        for seq in seqs:
            counts[seq] = counts.get(seq, 0) + 1
        duplicate_sequences = sorted(seq for seq, count in counts.items() if count > 1)
        observed_missing: list[int] = []
        if self.expected_documents is not None and seqs:
            observed_missing = sorted(set(range(1, self.expected_documents + 1)) - set(seqs))
        declared_missing = sorted(set(self.missing_sequences))
        missing_declaration_matches = declared_missing == observed_missing
        index_exclude_sequences = sorted(set(self.index_exclude_sequences))
        observed_sequences = set(seqs)
        invalid_index_exclude_sequences = sorted(set(index_exclude_sequences) - observed_sequences)
        complete = (
            not errors
            and not duplicate_sequences
            and (self.expected_documents is None or len(documents) == self.expected_documents)
            and not observed_missing
            and not declared_missing
            and missing_declaration_matches
        )
        report = {
            "course_id": self.course_id,
            "title": self.title,
            "content_kind": self.content_kind,
            "source_root": self.source_root,
            "manifest_path": self.manifest_path,
            "expected_documents": self.expected_documents,
            "available_documents": len(documents),
            "complete": complete,
            "explicit_missing_sequences": declared_missing,
            "observed_missing_sequences": observed_missing,
            "missing_declaration_matches": missing_declaration_matches,
            "index_exclude_sequences": index_exclude_sequences,
            "invalid_index_exclude_sequences": invalid_index_exclude_sequences,
            "duplicate_sequences": duplicate_sequences,
            "invalid_documents": errors,
            "first_sequence": min(seqs) if seqs else None,
            "last_sequence": max(seqs) if seqs else None,
        }
        if strict and (errors or duplicate_sequences or not missing_declaration_matches or invalid_index_exclude_sequences):
            raise ValueError(f"course validation failed: {report}")
        return report

    def db_config(self, inspection: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "provider": self.provider,
            "subject": self.subject,
            "course_year": self.course_year,
            "content_kind": self.content_kind,
            "source_uri": self.source_uri,
            "expected_documents": self.expected_documents,
            "available_documents": inspection["available_documents"],
            "complete": inspection["complete"],
            "explicit_missing_sequences": inspection["explicit_missing_sequences"],
            "observed_missing_sequences": inspection["observed_missing_sequences"],
            "index_exclude_sequences": inspection["index_exclude_sequences"],
            "metadata": self.metadata,
        }
