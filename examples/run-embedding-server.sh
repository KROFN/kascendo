#!/usr/bin/env bash
set -euo pipefail
: "${EMBEDDING_MODEL:?Set EMBEDDING_MODEL to a local Qwen3-Embedding-0.6B GGUF path}"
SERVER="${LLAMA_SERVER:-llama-server}"
exec "$SERVER" -m "$EMBEDDING_MODEL" --embedding \
  --host 127.0.0.1 --port 8091 \
  --ctx-size 8192 --batch-size 512 --ubatch-size 512 --no-webui
