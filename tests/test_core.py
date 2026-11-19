"""Offline smoke tests of real retrieval code on synthetic data."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from retrieval.adapter import ASRAdapter
from retrieval.chunker import TimestampChunker
from retrieval.engine import SearchEngine


def make_demo(path: Path) -> Path:
    payload = {
        "schema_version": 2, "status": "complete",
        "source": {
            "source_id": "synthetic_001", "title": "Demo arithmetic",
            "provider": "synthetic", "subject": "math", "course_year": "demo",
            "sequence": 1, "duration_seconds": 18,
            "source_urls": ["https://example.com/lesson/1"],
        },
        "asr": {"engine": "synthetic", "language": "ru"},
        "segments": [
            {"id": 0, "start": 0, "end": 3, "text": "Сегодня изучаем остаток при делении числа на три."},
            {"id": 1, "start": 3, "end": 6, "text": "Остаток может быть ноль один или два."},
            {"id": 2, "start": 6, "end": 9, "text": "Пример семь делится на три с остатком один."},
            {"id": 3, "start": 9, "end": 12, "text": "Проверяем вычисление обычным делением."},
            {"id": 4, "start": 12, "end": 15, "text": "Ответ записываем без округления."},
            {"id": 5, "start": 15, "end": 18, "text": "На этом разбор завершен."},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class FakeEmbedder:
    """Constant deterministic vectors. NOT a semantic quality benchmark."""
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def embed_query(self, query):
        return [1.0] + [0.0] * 1023


class PublicRetrievalTests(unittest.TestCase):
    def test_adapter_and_chunk_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            doc = ASRAdapter.load(make_demo(Path(td) / "lesson.json"))
            self.assertEqual(doc.source_id, "synthetic_001")
            self.assertEqual(len(doc.segments), 6)
            chunks = TimestampChunker(target_tokens=8, hard_max_tokens=20, overlap_segments=0).chunk(doc)
            self.assertGreater(len(chunks), 1)
            lookup = {seg.id: seg for seg in doc.segments}
            for chunk in chunks:
                self.assertEqual(chunk.time_start, lookup[chunk.segment_start_id].start)
                self.assertEqual(chunk.time_end, lookup[chunk.segment_end_id].end)
                self.assertEqual(
                    chunk.text,
                    " ".join(seg.text for seg in doc.segments
                             if chunk.segment_start_id <= seg.id <= chunk.segment_end_id),
                )

    def test_index_search_and_source_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc = ASRAdapter.load(make_demo(root / "lesson.json"), corpus="public_demo")
            engine = SearchEngine(root / "test.db", FakeEmbedder())
            try:
                engine.db.init_schema()
                chunks = TimestampChunker(target_tokens=8, hard_max_tokens=20, overlap_segments=0).chunk(doc)
                result = engine.db.index_document(doc, chunks, FakeEmbedder())
                self.assertEqual(result["chunks"], len(chunks))
                found = engine.search("остаток при делении на три", max_clips=3)
                self.assertTrue(found["clips"])
                self.assertTrue(any("остаток" in clip["text"].lower() for clip in found["clips"]))
                for clip in found["clips"]:
                    self.assertTrue(engine.roundtrip_clip(clip)["ok"])
                    self.assertEqual(clip["source_url"], "https://example.com/lesson/1")
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
