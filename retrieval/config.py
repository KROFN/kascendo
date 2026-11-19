from __future__ import annotations
import os
import re

SCHEMA_VERSION = 1
CHUNKER_VERSION = "asr-segments-v1"
EMBEDDING_MODEL = "Qwen3-Embedding-0.6B-Q8_0.gguf"
EMBEDDING_MODEL_SHA256 = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
EMBEDDING_DIM = 1024
QUERY_INSTRUCTION = (
    "Given a question about a Russian educational lesson, retrieve the transcript "
    "passages that directly answer or explain the question."
)
RRF_K = 60
SEMANTIC_MIN_SIMILARITY = 0.458
DEFAULT_ENDPOINT = os.environ.get("KASCENDO_EMBED_URL", "http://127.0.0.1:8091/v1/embeddings")
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)
STOPWORDS = {
    "а","без","бы","был","была","были","было","в","вам","вас","вот","все","всех",
    "где","да","для","до","его","ее","если","есть","же","за","и","из","или","как",
    "к","когда","ли","мне","мы","на","над","не","но","ну","о","об","он","она","они",
    "от","по","под","при","про","с","со","так","то","у","что","чтобы","это","этого",
    "эта","эти","этот","я","почему","какой","какая","какие","какое","может","можно",
}
