---
description: Install the advisory-board pipeline and build a local corpus
---

Help the user stand up the advisory-board system. This is a one-time setup that
installs a Python environment, a local model server, and then builds a corpus by
transcribing the configured podcast feeds (which can take hours to days depending
on how many episodes and the machine).

Steps:

1. Run the setup script (it needs no admin/sudo):
   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/setup.sh"
   ```
   It creates `$ADVISORY_HOME` (default `~/AdvisoryBoard`), a virtualenv with the
   dependencies, downloads a standalone Ollama binary, and copies the pipeline in.

2. Tell the user to **edit `$ADVISORY_HOME/feeds.yaml`** to choose their own shows
   (it starts from `feeds.example.yaml`). Each entry needs a `slug`, `person`,
   `show`, and RSS `feed_url`. Resolve a show's feed via the Apple Podcasts / iTunes
   Search API if they only have a name.

3. Build the corpus (idempotent + resumable — safe to stop/resume):
   ```bash
   cd "$ADVISORY_HOME" && source .venv/bin/activate
   python scripts/fetch_feeds.py       # pull episode metadata
   python scripts/transcribe.py        # transcribe (long-running; resumable)
   python scripts/chunk_embed.py       # make it searchable
   python scripts/build_indexes.py     # optional: per-voice theme summaries
   ```

4. Then questions can be answered with `/advisory-board "<question>"`.

Warn the user honestly about: transcription time (proportional to total audio and
CPU), that everything stays local, and that they should build their own corpus
rather than copying someone else's transcripts (copyright).
