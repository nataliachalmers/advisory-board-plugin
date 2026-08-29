# Advisory Board

Turn any set of podcast feeds into a **private, fully-local "advisory board."** Ask a
natural-language question and get **each expert's perspective**, where they agree or
disagree, and the **specific episodes + timestamps** to go listen to.

Everything runs on your machine — audio is transcribed locally, indexed into a local
vector database, and answered by a local model. **No content leaves your computer.**

Built originally for a five-voice dental practice-management board, but the feeds are
just config — point it at any shows you like.

---

## What's in this repo

```
.claude-plugin/       plugin + marketplace manifests (install via /plugin)
skills/advisory-board create the skill Claude uses to answer questions
commands/             /advisory-board and /advisory-board-setup slash commands
scripts/              the pipeline (fetch → transcribe → embed → index → ask)
feeds.example.yaml    template — copy to feeds.yaml and pick your shows
setup.sh              one-time installer (venv, deps, Ollama) — no admin needed
```

The **corpus is not included** and is `.gitignore`d — you build your own (see below).

## Install as a Claude Code plugin

```
/plugin marketplace add nataliachalmers/advisory-board-plugin
/plugin install advisory-board@advisory-board-marketplace
```

Then run the one-time setup and build your corpus:

```
/advisory-board-setup
```

…or do it by hand:

```bash
bash setup.sh                        # venv + deps + local model (macOS; see notes for Linux)
cd ~/AdvisoryBoard && source .venv/bin/activate
$EDITOR feeds.yaml                   # choose your shows (resolve RSS via iTunes Search API)
python scripts/fetch_feeds.py        # pull episode metadata
python scripts/transcribe.py         # transcribe (long-running; resumable, Ctrl-C safe)
python scripts/chunk_embed.py        # make it searchable
python scripts/build_indexes.py      # optional: per-voice theme summaries
```

## Ask it anything

```
/advisory-board "When should I hire an associate?"
```
or directly:
```bash
cd ~/AdvisoryBoard && source .venv/bin/activate
python scripts/ask.py "When should I hire an associate?"
```

## How it works

A five-stage, idempotent, resumable pipeline with a single SQLite source of truth:

`fetch_feeds` → `transcribe` → `chunk_embed` → `build_indexes` → `ask`

- **Transcription:** `faster-whisper` (`small.en` by default) on CPU — audio is deleted
  right after each episode is transcribed, so the footprint stays tiny.
- **Search:** local Chroma vector DB with on-device embeddings, tagged per voice.
- **Synthesis:** a local Ollama model (`llama3.2:3b` default) — fully private.

## Requirements

- **macOS or Linux**, Python 3.9+
- A few GB of disk for the vector DB (audio is not kept)
- CPU is fine; transcription time scales with total audio (a small model does clear
  speech several times faster than real-time). Everything is resumable.
- On low-RAM machines, run **one** local-LLM job at a time.

**Linux note:** `setup.sh` fetches Ollama for macOS; on Linux install it from
<https://ollama.com/download> and `ollama pull llama3.2:3b`. The rest is identical.

## Keeping it current

`scripts/update.sh` (fetch → transcribe → embed, lock-guarded) pulls new episodes.
Schedule it however you like — cron, a launchd/systemd timer, or run it by hand.

## ⚠️ Copyright

This repo is the **pipeline only** (MIT). Podcast audio and transcripts belong to their
creators. The tool transcribes audio locally for **private use** — **do not redistribute
audio, transcripts, or a built vector database.** Everyone builds their own corpus from
feeds they're entitled to access. See `LICENSE`.
