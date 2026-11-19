# Architecture

This repository contains the **real Python retrieval/API component** of
K.ascendo. It is not a screenshot-based demo, a static portfolio mockup, or
the full private educational product.

The pipeline:
1. Canonical ASR v2 JSON transcripts with ordered segments and timestamps.
2. Timestamp-aware chunking with stable hashes and bounded overlap.
3. libsql database: source records, FTS5 lexical index and 1024-dimensional
   vector index (cosine metric).
4. Local embedding provider using the Qwen3-Embedding-0.6B embedding protocol.
5. Hybrid retrieval, lexical/semantic ranking, temporal deduplication,
   metadata filters and source-grounded clip generation.
6. Source roundtrip validation against original transcript segments.
7. Local HTTP API for search, course metadata and learning progress.

See the actual modules in retrieval/ and the HTTP handlers in serve.py.

The offline integration test uses a deterministic fake embedder for the
data flow only. It does not stand in for semantic relevance evaluation.

## Not implemented here

An autonomous portal agent, full LLM answer generator and per-user ERP RBAC
are not part of this subsystem. A source citation is not an authorization
boundary. The private embedding model files, transcript corpus and operational
databases are also not redistributed.
