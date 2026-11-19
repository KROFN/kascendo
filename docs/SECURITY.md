# Security and publication boundaries

This is a **clean public code export**, not the historical private Git
repository. The source history contains internal evaluation artifacts
that must never be pushed into the public repository.

Excluded:
- original video transcripts, audio and teaching materials;
- database files, embeddings, Qwen weights and evaluation query logs;
- real service configuration, private absolute paths, API keys and tokens;
- user accounts and private learning progress.

The public fixture is synthetic, including its text and test database.
All tests run without internet access to real sources.

The HTTP service binds to 127.0.0.1 by default. It is not an authenticated
public-facing service. Do not bind it to a public network interface without
adding authentication, authorization and isolation.

The scripts/public_preflight.py tool checks an allowlist of paths, size caps
and common secret patterns. It does not prove that every kind of secret is
impossible; human inspection of changes remains necessary.
