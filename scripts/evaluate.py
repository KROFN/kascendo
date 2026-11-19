"""Evaluate a local retrieval database against a user-supplied JSON query set.

The private K.ascendo evaluation corpus is deliberately not shipped.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from retrieval.embed import EmbedClient
from retrieval.engine import SearchEngine


def overlaps(clip: dict[str, Any], label: dict[str, Any]) -> bool:
    return (
        clip["source_id"] == label["source_id"]
        and clip["segment_end_id"] >= label["segment_start_id"]
        and clip["segment_start_id"] <= label["segment_end_id"]
    )


def first_matching_rank(clips: list[dict[str, Any]], labels: list[dict[str, Any]]) -> int | None:
    return next(
        (n for n, clip in enumerate(clips, 1)
         if any(overlaps(clip, label) for label in labels)),
        None,
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [row for row in rows if row["ground_truth"]]
    negative = [row for row in rows if not row["ground_truth"]]
    positive_hits = [first_matching_rank(row["clips"], row["ground_truth"]) for row in positive]
    roundtrip_checks = [ok for row in rows for ok in row["roundtrip_ok"]]
    latencies = [row["latency_ms"] for row in rows]
    return {
        "total": len(rows),
        "positive": len(positive),
        "negative": len(negative),
        "top1": sum(rank == 1 for rank in positive_hits),
        "top3": sum(rank is not None and rank <= 3 for rank in positive_hits),
        "negative_rejected": sum(not row["clips"] for row in negative),
        "roundtrip_passed": sum(roundtrip_checks),
        "roundtrip_checked": len(roundtrip_checks),
        "median_ms": statistics.median(latencies) if latencies else None,
    }


def evaluate(db: Path, queryset: Path, max_clips: int = 3) -> dict[str, Any]:
    payload = json.loads(queryset.read_text(encoding="utf-8"))
    queries = payload["queries"]
    if not isinstance(queries, list) or not queries:
        raise ValueError("evaluation requires nonempty queries list")
    if not 1 <= max_clips <= 10:
        raise ValueError("max_clips must be between 1 and 10")
    engine = SearchEngine(db, EmbedClient(timeout=30, retries=1))
    cases = []
    try:
        for row in queries:
            query = row["query"]
            labels = row.get("ground_truth", [])
            found = engine.search(query, max_clips=max_clips)
            clips = found["clips"]
            checks = [bool(engine.roundtrip_clip(clip)["ok"]) for clip in clips]
            cases.append({
                "id": row.get("id", query),
                "ground_truth": labels,
                "clips": clips,
                "roundtrip_ok": checks,
                "latency_ms": float(found["diagnostics"]["total_ms"]),
            })
    finally:
        engine.close()
    return summarize(cases)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--max-clips", type=int, default=3)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = evaluate(args.db, args.queries, args.max_clips)
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(data + "\n", encoding="utf-8")
    print(data)


if __name__ == "__main__":
    main()
