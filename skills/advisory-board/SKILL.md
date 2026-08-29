---
name: advisory-board
description: Ask your local podcast "advisory board" any question and get each expert's perspective with the specific episodes and timestamps to go listen to. Use whenever the user asks a question that the configured shows would cover, or when they type /advisory-board. Requires the advisory-board pipeline to be installed and a corpus built (see /advisory-board-setup).
---

# Advisory Board

A private, fully local knowledge system over your chosen podcast feeds. It answers
questions by retrieving from a local vector DB and synthesizing with a local model —
nothing leaves the machine.

The install location is `$ADVISORY_HOME` (default `~/AdvisoryBoard`).

## Answering a question

1. Confirm the system is installed; if `$ADVISORY_HOME` doesn't exist, tell the user
   to run `/advisory-board-setup` first.
2. Make sure the local model server is up:
   ```bash
   curl -s http://localhost:11434/api/tags >/dev/null || (cd "${ADVISORY_HOME:-$HOME/AdvisoryBoard}" && nohup ./bin/ollama serve >/dev/null 2>&1 &)
   ```
3. Run the query from the project's virtualenv:
   ```bash
   cd "${ADVISORY_HOME:-$HOME/AdvisoryBoard}" && source .venv/bin/activate
   python scripts/ask.py "<the user's question>"
   ```
4. Present the result faithfully: each voice's attributed perspective, where they
   agree/disagree, and the "episodes to listen to" list with timestamps. Do not add
   claims beyond what `ask.py` returns.

## Operating notes

- **Synthesis is slow on small machines** (a local 3B model can take a minute or two).
  Wait for it; don't retry.
- **One local-LLM job at a time** on low-RAM machines — don't run `build_indexes.py`
  while a query runs.
- **Retrieval is instant.** `ask.py` prints cited passages even without synthesis.

## Keeping it current

If the weekly updater is installed, new episodes are pulled automatically. On demand:
```bash
cd "${ADVISORY_HOME:-$HOME/AdvisoryBoard}" && bash scripts/update.sh
```
