#!/bin/bash
# One-time setup for the advisory-board pipeline. No admin/sudo required.
# Creates $ADVISORY_HOME with a venv, dependencies, a standalone Ollama binary,
# and the pipeline scripts. Does NOT transcribe anything — that's a later step.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${ADVISORY_HOME:-$HOME/AdvisoryBoard}"
OLLAMA_VER="${OLLAMA_VERSION:-v0.30.8}"
MODEL="${ADVISORY_LLM:-llama3.2:3b}"

echo "==> Installing advisory-board into: $HOME_DIR"
mkdir -p "$HOME_DIR"/{scripts,data,logs,indexes,bin}

echo "==> Copying pipeline"
cp "$SRC"/scripts/*.py "$HOME_DIR/scripts/"
cp "$SRC"/scripts/update.sh "$HOME_DIR/scripts/" 2>/dev/null || true
[ -f "$HOME_DIR/feeds.yaml" ] || cp "$SRC/feeds.example.yaml" "$HOME_DIR/feeds.yaml"

echo "==> Creating Python virtualenv"
python3 -m venv "$HOME_DIR/.venv"
# shellcheck disable=SC1091
source "$HOME_DIR/.venv/bin/activate"
python -m pip install --quiet --upgrade pip wheel
echo "==> Installing dependencies (faster-whisper, chromadb, feedparser, ...)"
python -m pip install --quiet faster-whisper feedparser requests pyyaml tqdm chromadb

# --- local model server (Ollama) ---
OS="$(uname -s)"; ARCH="$(uname -m)"
if [ "$OS" = "Darwin" ]; then
  if [ ! -x "$HOME_DIR/bin/ollama" ]; then
    echo "==> Downloading Ollama ($OLLAMA_VER, macOS universal)"
    curl -fsSL "https://github.com/ollama/ollama/releases/download/${OLLAMA_VER}/ollama-darwin.tgz" -o /tmp/ollama-darwin.tgz
    tar -xzf /tmp/ollama-darwin.tgz -C "$HOME_DIR/bin/"
  fi
  echo "==> Starting Ollama and pulling model: $MODEL"
  nohup "$HOME_DIR/bin/ollama" serve >"$HOME_DIR/logs/ollama.log" 2>&1 &
  sleep 5
  "$HOME_DIR/bin/ollama" pull "$MODEL" || echo "   (pull failed — run it later: $HOME_DIR/bin/ollama pull $MODEL)"
else
  echo "==> Non-macOS detected ($OS/$ARCH)."
  echo "    Install Ollama from https://ollama.com/download, then: ollama pull $MODEL"
fi

cat <<EOF

==========================================================
  Setup complete.  ADVISORY_HOME = $HOME_DIR

  Next:
    1. Edit  $HOME_DIR/feeds.yaml   (choose your shows)
    2. cd "$HOME_DIR" && source .venv/bin/activate
    3. python scripts/fetch_feeds.py      # pull episode metadata
       python scripts/transcribe.py       # transcribe (long; resumable)
       python scripts/chunk_embed.py      # make it searchable
       python scripts/build_indexes.py    # optional per-voice summaries
    4. Ask:  python scripts/ask.py "your question"
             or use  /advisory-board "your question"

  Everything stays local. Build your own corpus — don't copy transcripts.
==========================================================
EOF
