# K.ascendo Retrieval

A **real, executable Python retrieval service**, not a portfolio mockup.

K.ascendo Retrieval indexes time-coded video transcripts and finds relevant
moments with exact source URLs and timestamps. It combines FTS5 lexical
search, 1024-dimensional embeddings, ranking, temporal de-duplication and
source roundtrip checks.

**Scope:** This repository contains the Python retrieval subsystem,
its local HTTP API, course/progress helpers and offline tests. It does not
claim to be the whole private K.ascendo learning application.

## What is inside

- `retrieval/`: ASR parsing, timestamp chunker, libsql FTS5/vector storage,
  query ranking, source-grounded clip assembly, learning-unit and inbox modules
- `serve.py`: local HTTP search and course endpoints
- `tests/`: unittest cases, including an indexed search and verified
  source roundtrip on synthetic material
- `scripts/`: publication preflight and evaluation tools
- `docs/`: architecture, benchmark results and security boundaries
- `.github/workflows/ci.yml`: checks on every push and pull request

## Run the checks

Python 3.12+ on a platform supported by `libsql==0.1.11`.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/public_preflight.py
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q retrieval serve.py tests scripts
.venv/bin/python -m unittest discover -s tests -v
```

On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`.

The offline tests do not download a model, request outside APIs or use
private course data. One test runs a full indexing and search cycle with
**fake deterministic embeddings**. It tests plumbing, not semantic quality.

To inspect commands:

```bash
.venv/bin/python -m retrieval.cli --help
```

## Reproducible demo with a local embedding model

The repository includes a tiny **synthetic** transcript and course manifest,
not the original educational data. For an actual semantic-search run, install
`llama-server` and obtain a compatible Qwen3-Embedding-0.6B GGUF yourself:

```bash
export EMBEDDING_MODEL=/path/to/Qwen3-Embedding-0.6B-Q8_0.gguf
bash examples/run-embedding-server.sh
```

In a second terminal after the embedding endpoint is healthy:

```bash
.venv/bin/python -m retrieval.cli --db demo.db inspect-course examples/demo-course.json
.venv/bin/python -m retrieval.cli --db demo.db index-course examples/demo-course.json
.venv/bin/python -m retrieval.cli --db demo.db search "остаток при делении" --roundtrip
```

Local model weights are intentionally not downloaded automatically. The demo
queries test flow, not retrieval quality on the private 121-document corpus.

## Local deployment with your own data

1. Prepare canonical ASR v2 JSON segments and a compatible course manifest.
2. Run a local Qwen3-compatible embedding service that returns 1024-element
   vectors; it is not included in this repo.
3. Configure `KASCENDO_EMBED_URL`, `KASCENDO_DB` and `KASCENDO_COURSE_DIR`.
4. Index your course using the CLI, then run `python serve.py`.
5. Keep the API on loopback unless you provide access control yourself.

The service is not advertised as secure public hosting or a turnkey SaaS.

## Test and evaluation reality

The private v0.4 corpus had **121 unique searchable sources, 3094 embeddings**
and **108.37ms median full-path latency** during its frozen gate. The exact
source/timestamp roundtrip passed at 100%.

**However, the strict full-corpus quality gate failed:**
Top-1 15/44, Top-3 21/44, hard-negative rejection 6/8.
K.ascendo v0.4 is DOGFOOD, not a quality-certified release.
See [measured results](docs/QUALITY.md).

This is engineering evidence, not a claim of commercial production-grade RAG,
corporate ERP access control or autonomous agent completion.

## Why the public corpus is small

The original course material and benchmark queries are private or may
belong to their authors. They are excluded on purpose, along with
databases, logs, model weights, configuration and API credentials.

Public test inputs are synthetic, and the Git history begins cleanly so
old internal artifacts cannot leak through previous commits.

## Licensing

No open-source license has yet been selected by the owner. Public visibility
does not imply a grant of reuse or redistribution rights.
