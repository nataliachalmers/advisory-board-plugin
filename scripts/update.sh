#!/bin/bash
# Auto-update: pull new episodes, transcribe them, embed them.
# Idempotent and lock-guarded so runs never overlap (manual or scheduled).
# Deliberately does NOT run build_indexes (a slow local-LLM job) — rebuild the
# per-voice summaries occasionally by hand: python scripts/build_indexes.py
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
mkdir -p logs data

# atomic lock (macOS has no flock); stale-lock guard after 6h
LOCK="$ROOT/data/.update.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || { echo "$(date) update: locked, skip" >>logs/update.log; exit 0; }
  else
    echo "$(date) update: another run in progress, skipping" >>logs/update.log; exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

source "$ROOT/.venv/bin/activate"
{
  echo "==================== update run $(date) ===================="
  before=$(sqlite3 data/episodes.db "SELECT COUNT(*) FROM episodes" 2>/dev/null)
  python scripts/fetch_feeds.py
  after=$(sqlite3 data/episodes.db "SELECT COUNT(*) FROM episodes" 2>/dev/null)
  echo ">> feeds: $before -> $after episodes known"
  python scripts/transcribe.py --model small.en --rest 8
  python scripts/chunk_embed.py
  echo ">> chunks now: $(python -c "import chromadb;print(chromadb.PersistentClient(path='data/chroma').get_or_create_collection('advisory').count())" 2>/dev/null)"
  echo "==================== update done $(date) ===================="
} >>logs/update.log 2>&1
