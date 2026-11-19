from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str

@dataclass(frozen=True)
class CanonicalDocument:
    source_id: str
    corpus: str
    title: str
    provider: str
    subject: str
    course_year: str
    source_variant: str | None
    sequence: int | None
    telegram_message_id: int | None
    duration_seconds: float
    source_path: str
    content_hash: str
    metadata: dict[str, Any]
    segments: tuple[Segment, ...]

class ASRAdapter:
    @staticmethod
    def load(path: str | Path, corpus: str = "100b_profile") -> CanonicalDocument:
        p = Path(path)
        raw_bytes = p.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
        if raw.get("schema_version") != 2 or raw.get("status") != "complete":
            raise ValueError("expected canonical ASR schema_version=2 status=complete")
        src = raw.get("source") or {}
        rows = raw.get("segments") or []
        if not rows:
            raise ValueError("ASR transcript has no segments")
        segments: list[Segment] = []
        seen: set[int] = set()
        last_end = -1.0
        for row in rows:
            sid = int(row["id"])
            start, end = float(row["start"]), float(row["end"])
            text = str(row.get("text") or "").strip()
            if sid in seen or end < start or start + 1e-6 < last_end:
                raise ValueError(f"invalid/non-monotonic ASR segment id={sid}")
            seen.add(sid)
            last_end = end
            segments.append(Segment(sid, start, end, text))
        source_id = str(src.get("source_id") or p.stem)
        asr = raw.get("asr") or {}
        metadata = {
            "provider": src.get("provider"), "subject": src.get("subject"),
            "course_year": src.get("course_year"), "sequence": src.get("sequence"),
            "telegram_message_id": src.get("telegram_message_id"),
            "paired_full_message_id": src.get("paired_full_message_id"),
            "source_variant": src.get("source_variant"), "audio_file": src.get("file_name"),
            "duration_seconds": src.get("duration_seconds"),
            "source_urls": [str(x) for x in (src.get("source_urls") or []) if str(x).strip()],
            "downloaded_from": src.get("downloaded_from"),
            "asr_engine": asr.get("engine"), "asr_model": asr.get("model"), "language": asr.get("language"),
        }
        return CanonicalDocument(
            source_id=source_id, corpus=corpus, title=str(src.get("title") or p.stem),
            provider=str(src.get("provider") or ""), subject=str(src.get("subject") or ""),
            course_year=str(src.get("course_year") or ""), source_variant=src.get("source_variant"),
            sequence=int(src["sequence"]) if src.get("sequence") is not None else None,
            telegram_message_id=int(src["telegram_message_id"]) if src.get("telegram_message_id") is not None else None,
            duration_seconds=float(src.get("duration_seconds") or segments[-1].end),
            source_path=str(p.resolve()), content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            metadata=metadata, segments=tuple(segments),
        )
