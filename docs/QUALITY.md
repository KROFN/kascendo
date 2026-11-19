# Measured quality and release status

These are real measured results from the private v0.4 full-corpus release
report, not measurements run against the two-item synthetic fixture.

| Measurement | Actual result |
| --- | --- |
| Canonical videos | 122 |
| Unique searchable documents | 121 |
| Chunks / embeddings | 3094 / 3094 |
| Frozen evaluation queries | 52 (44 relevant/ambiguous, 8 negative) |
| Strict Top-1 | 15/44 (34.1%) |
| Strict Top-3 | 21/44 (47.7%) |
| Hard-negative rejection | 6/8 |
| Source/timestamp exact roundtrip | 100% |
| Median full-path latency | 108.37 ms |
| p95 latency | 127.10 ms |

**Release gate verdict: FAIL.** Operational state: DOGFOOD.

A baseline label might identify one expected video while the bigger corpus
contains other legitimate lessons explaining the same point. This explains
some strict-label misses, but does not turn FAIL into PASS.

Two out-of-domain questions, one about chemistry and one about Faraday's
law, produced genuine false positives. Query relevance and abstention
therefore require further work. The production-ready claim is intentionally
not made.

Next required experiment: fresh held-out queries from real dogfood use,
with multiple acceptable ground-truth sources where appropriate, plus
explicit out-of-domain labels.

The public source code and offline integration tests are executable.
The original 121-video benchmark is not independently reproducible
without the private corpus and vector database; those are not included.
